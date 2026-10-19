"""Report library: catalog + custom folder trees (Central and per department, any depth),
paste-a-design import, and the report viewer. Every edit, open, download and export is logged.

The folder engine (trees, folders, items, paste-a-design) is shared with the masters library: each
section has its own trees, routes (/reports/..., /masters/...) and permissions."""
import csv
import io
import json
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import audit, db
from ..config import settings
from ..data import gateway
from ..data.contracts import freshness
from ..iam.context import Ctx
from ..web import envelope, require
from . import report_import as rimp
from .portals import visible as portal_visible

router = APIRouter(prefix="/api/v1")
VIEW = require("reports.view")


# ---------------------------------------------------------------- permissions
# The folder engine serves two sections with separate trees and permissions: the report library
# ("reports") and the masters library ("masters"). A folder's section never changes.
SECTIONS = ("reports", "masters")


def can_see_all_trees(ctx: Ctx, section: str = "reports") -> bool:
    if section == "masters":
        return ctx.can("masters.view_all") or ctx.can("masters.tree.all")
    return ctx.can("reports.tree.all") or ctx.can("ct.view_all")


def can_see_tree(ctx: Ctx, tree: str, section: str = "reports") -> bool:
    # custom (admin-created) top-level trees are visible to everyone, like Central.
    if tree == "central" or tree.startswith("custom:") or can_see_all_trees(ctx, section):
        return True
    return ":" in tree and tree.split(":", 1)[1] in ctx.depts


def can_edit_tree(ctx: Ctx, tree: str, section: str = "reports") -> bool:
    if ctx.can(section + ".tree.all"):
        return True
    if tree == "central":
        return ctx.can(section + ".tree.central")
    if tree.startswith("custom:"):
        return False  # custom top-level trees are managed by portal admins only (section.tree.all)
    return ctx.can(section + ".tree.dept") and ":" in tree and tree.split(":", 1)[1] in ctx.depts


def _q(tree: str) -> str:
    """Tree codes are validated by _check_tree / come from the database; quote for a literal."""
    return "'%s'" % tree.replace("'", "''")


def _check_tree(tree: str):
    if tree == "central":
        return
    if tree.startswith("dept:") and tree[5:].replace("_", "").isalpha() and tree[5:].isupper():
        return
    if tree.startswith("custom:"):
        slug = tree[7:]
        if slug and len(slug) <= 40 and slug.isalnum() and slug == slug.lower():
            return
    raise HTTPException(422, "Unknown tree.")


def _need_edit(ctx, tree, section="reports"):
    if not can_edit_tree(ctx, tree, section):
        raise HTTPException(403, "You cannot edit this folder tree.")


def _need_folder_admin(ctx, section="reports"):
    # Creating directories is restricted to the portal administrator (section.folders.create),
    # independent of who may otherwise edit the tree's contents.
    if not ctx.can(section + ".folders.create"):
        raise HTTPException(403, "Only a portal administrator can create directories.")


def _roles_ok(audience, ctx, section="reports"):
    return not audience or bool(set(audience) & set(ctx.roles)) or ctx.can(section + ".tree.all")


# ---------------------------------------------------------------- trees
# Tree names live in nav.tree_name (migration 0006) and are renamed from /manage/trees. A tree without a row
# shows its default: "Central", or the department's name.
CENTRAL_DEFAULT = "Central"


def tree_names(cur, section: str) -> dict:
    cur.execute("select tree, name, name_hi from nav.tree_name where section = %s", (section,))
    return {r["tree"]: r for r in cur.fetchall()}


def tree_name(cur, section: str, tree: str = "central") -> str:
    r = tree_names(cur, section).get(tree)
    return r["name"] if r else CENTRAL_DEFAULT


def _trees(ctx, section):
    with db.tx() as cur:
        cur.execute("select code, name from iam.org_node where node_type = 'dept' and is_active and code <> 'UNASSIGNED'"
                    " order by name")
        depts = cur.fetchall()
        cur.execute("select f.tree, count(distinct f.id) folders, count(i.id) items from nav.folder f"
                    " left join nav.item i on i.folder_id = f.id where f.section = %s and not f.is_archived group by f.tree",
                    (section,))
        sizes = {r["tree"]: r for r in cur.fetchall()}
        names = tree_names(cur, section)

    def size(t):
        s = sizes.get(t) or {}
        return {"folder_count": s.get("folders", 0), "item_count": s.get("items", 0)}

    def label(t, default):
        r = names.get(t)
        return {"name": r["name"] if r else default, "name_hi": r["name_hi"] if r else None, "default_name": default}
    out = [{"tree": "central", **label("central", CENTRAL_DEFAULT), "can_edit": can_edit_tree(ctx, "central", section),
            **size("central")}]
    for d in depts:
        t = "dept:" + d["code"]
        if can_see_tree(ctx, t, section):
            out.append({"tree": t, **label(t, d["name"]), "can_edit": can_edit_tree(ctx, t, section),
                        "mine": d["code"] in ctx.depts, **size(t)})
    # Admin-created top-level trees (custom:<slug>), registered by name in nav.tree_name.
    for t, r in sorted(names.items(), key=lambda kv: (kv[1]["name"] or "").lower()):
        if t.startswith("custom:") and can_see_tree(ctx, t, section):
            out.append({"tree": t, "name": r["name"], "name_hi": r["name_hi"], "default_name": r["name"],
                        "can_edit": can_edit_tree(ctx, t, section), "custom": True, **size(t)})
    return {"data": out}


class TreeNameIn(BaseModel):
    name: str | None = Field(None, max_length=60)      # empty or null = back to the default name
    name_hi: str | None = Field(None, max_length=60)


class TreeCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    name_hi: str | None = Field(None, max_length=60)


def _all_tree_names(cur, section: str) -> dict:
    """Current display name of every tree (central + departments + custom), keyed by tree code."""
    names = tree_names(cur, section)
    current = {"central": names["central"]["name"] if "central" in names else CENTRAL_DEFAULT}
    cur.execute("select code, name from iam.org_node where node_type = 'dept' and is_active and code <> 'UNASSIGNED'")
    for d in cur.fetchall():
        t = "dept:" + d["code"]
        current[t] = names[t]["name"] if t in names else d["name"]
    for t, r in names.items():
        if t.startswith("custom:"):
            current[t] = r["name"]
    return current


def _create_tree(cur, body: TreeCreateIn, ctx: Ctx, section: str, request: Request):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Type a name for the section.")
    current = _all_tree_names(cur, section)
    if any((n or "").strip().lower() == name.lower() for n in current.values()):
        raise HTTPException(409, "A tree called '%s' already exists." % name)
    base = "".join(c for c in name.lower() if c.isalnum())[:32] or "section"
    slug, n = base, 1
    while ("custom:" + slug) in current:
        n += 1
        slug = base + str(n)
    tree = "custom:" + slug
    cur.execute("insert into nav.tree_name (section, tree, name, name_hi, updated_by) values (%s,%s,%s,%s,%s)",
                (section, tree, name, (body.name_hi or "").strip() or None, ctx.user_id))
    audit.action(cur, ctx, section + ".tree.create", "tree", tree, {"name": name}, req=request)
    return {"data": {"tree": tree, "name": name}}


def _rename_tree(tree: str, body: TreeNameIn, ctx: Ctx, section: str, request: Request):
    _check_tree(tree)
    _need_edit(ctx, tree, section)
    name, name_hi = (body.name or "").strip(), (body.name_hi or "").strip() or None
    with db.tx() as cur:
        cur.execute("select name from nav.tree_name where section = %s and tree = %s for update", (section, tree))
        old = cur.fetchone()
        if not name:
            cur.execute("delete from nav.tree_name where section = %s and tree = %s", (section, tree))
        else:
            # Two trees with one name would be impossible to tell apart in the tree picker.
            current = _all_tree_names(cur, section)
            if any(t != tree and (n or "").strip().lower() == name.lower() for t, n in current.items()):
                raise HTTPException(409, "Another tree is already called '%s'." % name)
            cur.execute("insert into nav.tree_name (section, tree, name, name_hi, updated_by) values (%s,%s,%s,%s,%s)"
                        " on conflict (section, tree) do update set name = excluded.name, name_hi = excluded.name_hi,"
                        " updated_at = now(), updated_by = excluded.updated_by", (section, tree, name, name_hi, ctx.user_id))
        audit.action(cur, ctx, section + ".tree.rename", "tree", tree,
                     {"from": old["name"] if old else None, "to": name or None, "name_hi": name_hi}, req=request)
    return {"ok": True}


def _load_tree(cur, tree, ctx, section="reports"):
    cur.execute("select f.*, (select count(*) from nav.item i where i.folder_id = f.id) item_count from nav.folder f"
                " where f.section = %s and f.tree = %s and not f.is_archived order by f.sort_order, lower(f.name)",
                (section, tree))
    folders = cur.fetchall()
    hidden = {f["id"] for f in folders if not _roles_ok(f["audience_roles"], ctx, section)}
    # a hidden folder hides its whole subtree
    hidden_paths = [f["path"] for f in folders if f["id"] in hidden]
    folders = [f for f in folders if not any(f["path"].startswith(p) for p in hidden_paths)]
    ids = [f["id"] for f in folders]
    cur.execute("""select i.*, r.code r_code, r.name r_name, r.kind r_kind, r.status r_status, r.description r_desc,
                          r.owner_dept r_owner, r.audience_roles r_aud, p.id p_id, p.code p_code, p.name p_name,
                          p.url p_url, p.status p_status, p.is_active p_active, p.audience_roles p_aud, p.is_placeholder p_ph,
                          m.code m_code, m.name m_name, m.status m_status, m.description m_desc, m.owner_dept m_owner,
                          m.audience_roles m_aud, m.layer m_layer, m.sensitivity m_sens, m.row_count m_rows,
                          m.steward_name m_steward, m.profiled_at m_profiled
                   from nav.item i left join rpt.report r on r.id = i.report_id left join portal.portal p on p.id = i.portal_id
                   left join mst.master m on m.id = i.master_id
                   where i.folder_id = any(%s) order by i.sort_order, i.created_at""", (ids,))
    items = []
    for i in cur.fetchall():
        if i["kind"] == "report":
            if i["r_status"] == "deprecated" or not _roles_ok(i["r_aud"], ctx, section):
                continue
            extra = {"report": {"code": i["r_code"], "name": i["r_name"], "kind": i["r_kind"], "status": i["r_status"],
                                "description": i["r_desc"], "owner_dept": i["r_owner"]}}
        elif i["kind"] == "master":
            if i["m_status"] == "deprecated" or (i["m_status"] == "draft" and not ctx.can("masters.manage")) \
                    or not _roles_ok(i["m_aud"], ctx, "masters"):
                continue
            extra = {"master": {"code": i["m_code"], "name": i["m_name"], "status": i["m_status"],
                                "description": i["m_desc"], "owner_dept": i["m_owner"], "layer": i["m_layer"],
                                "sensitivity": i["m_sens"], "row_count": i["m_rows"], "steward_name": i["m_steward"],
                                "profiled_at": i["m_profiled"].isoformat() if i["m_profiled"] else None}}
        elif i["kind"] == "portal":
            if not portal_visible({"is_active": i["p_active"], "audience_roles": i["p_aud"]}, ctx):
                continue
            extra = {"portal": {"id": str(i["p_id"]), "code": i["p_code"], "name": i["p_name"],
                                "url": None if i["p_ph"] else i["p_url"], "status": i["p_status"]}}
        else:
            extra = {"url": i["url"]}
        ref = extra.get("report") or extra.get("master") or extra.get("portal") or {}
        items.append({"id": str(i["id"]), "folder_id": str(i["folder_id"]), "kind": i["kind"], "sort_order": i["sort_order"],
                      "title": i["title"] or ref.get("name") or i["url"], **extra})
    return folders, items


def _tree(tree, ctx, section):
    _check_tree(tree)
    if not can_see_tree(ctx, tree, section):
        raise HTTPException(403, "You cannot see this folder tree.")
    with db.tx() as cur:
        folders, items = _load_tree(cur, tree, ctx, section)
    return {"data": {"tree": tree, "section": section, "can_edit": can_edit_tree(ctx, tree, section),
                     "folders": [{"id": str(f["id"]), "parent_id": str(f["parent_id"]) if f["parent_id"] else None,
                                  "name": f["name"], "name_hi": f["name_hi"], "description": f["description"], "icon": f["icon"],
                                  "sort_order": f["sort_order"], "audience_roles": f["audience_roles"],
                                  "item_count": f["item_count"]} for f in folders],
                     "items": items}}


def _search(q, ctx, section):
    q = q.strip().lower()
    if len(q) < 2:
        return {"data": []}
    out = []
    with db.tx() as cur:
        cur.execute("select distinct tree from nav.folder where section = %s and not is_archived", (section,))
        for t in [r["tree"] for r in cur.fetchall() if can_see_tree(ctx, r["tree"], section)]:
            folders, items = _load_tree(cur, t, ctx, section)
            byid = {f["id"]: f for f in folders}

            def names(fid):
                chain, f = [], byid.get(fid)
                while f:
                    chain.append(f["name"])
                    f = byid.get(f["parent_id"])
                return list(reversed(chain))
            for f in folders:
                if q in f["name"].lower() or q in (f["name_hi"] or "").lower():
                    out.append({"type": "folder", "tree": t, "folder_id": str(f["id"]), "path_names": names(f["id"]),
                                "title": f["name"]})
            for i in items:
                codes = [(i.get(k) or {}).get("code", "") for k in ("report", "master")]
                if q in (i["title"] or "").lower() or any(q in c.lower() for c in codes if c):
                    out.append({"type": "item", "tree": t, "folder_id": i["folder_id"],
                                "path_names": names(uuid.UUID(i["folder_id"])), "title": i["title"],
                                "report_code": (i.get("report") or {}).get("code"),
                                "master_code": (i.get("master") or {}).get("code"),
                                "portal_id": (i.get("portal") or {}).get("id"), "url": i.get("url")})
    return {"data": out[:100]}


# ---------------------------------------------------------------- folder editing
class FolderIn(BaseModel):
    tree: str
    parent_id: str | None = None
    name: str = Field(min_length=1, max_length=120)
    name_hi: str | None = None
    description: str = ""
    icon: str | None = None
    audience_roles: list[str] = []


def _place(cur, table, parent_col, parent_id, moved_id, position):
    """Put moved_id at 0-based `position` among its siblings and renumber them 10, 20, 30 ..."""
    extra = " and not is_archived" if table == "nav.folder" else ""
    cur.execute("select id from %s where %s is not distinct from %%s%s order by sort_order, id" % (table, parent_col, extra),
                (parent_id,))
    ids = [r["id"] for r in cur.fetchall() if str(r["id"]) != str(moved_id)]
    pos = len(ids) if position is None else max(0, min(int(position), len(ids)))
    ids.insert(pos, uuid.UUID(str(moved_id)))
    for i, x in enumerate(ids):
        cur.execute("update %s set sort_order = %%s where id = %%s" % table, ((i + 1) * 10, x))


def _folder(cur, fid, lock=False, section=None):
    cur.execute("select * from nav.folder where id = %s and not is_archived" + (" for update" if lock else ""), (fid,))
    f = cur.fetchone()
    if not f or (section and f["section"] != section):
        raise HTTPException(404, "Folder not found.")
    return f


def _create_folder(cur, tree, parent, name, ctx, section="reports", **kw):
    cur.execute("select id from nav.folder where section = %s and tree = %s and parent_id is not distinct from %s"
                " and lower(name) = lower(%s) and not is_archived", (section, tree, parent["id"] if parent else None, name))
    if cur.fetchone():
        raise HTTPException(409, "A folder named '%s' already exists here." % name)
    fid = uuid.uuid4()
    path = (parent["path"] if parent else "/") + str(fid) + "/"
    cur.execute("select coalesce(max(sort_order), 0) + 10 n from nav.folder where section = %s and tree = %s"
                " and parent_id is not distinct from %s", (section, tree, parent["id"] if parent else None))
    order = cur.fetchone()["n"]
    cur.execute("insert into nav.folder (id, section, tree, parent_id, name, name_hi, description, icon, audience_roles, path,"
                " sort_order, updated_by) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",
                (fid, section, tree, parent["id"] if parent else None, name.strip(), kw.get("name_hi"),
                 kw.get("description", ""), kw.get("icon"), kw.get("audience_roles", []), path, order,
                 getattr(ctx, "user_id", None)))
    return cur.fetchone()


def _sibling_scope(f) -> str:
    return "section = %s and tree = %s and parent_id" % (_q(f["section"]), _q(f["tree"]))


class FolderUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    name_hi: str | None = None
    description: str | None = None
    icon: str | None = None
    audience_roles: list[str] | None = None


class MoveIn(BaseModel):
    parent_id: str | None = None
    sort_order: int | None = None


class ItemIn(BaseModel):
    kind: str = Field(pattern=r"^(report|portal|link|master)$")
    report_code: str | None = None
    master_code: str | None = None
    portal_id: str | None = None
    url: str | None = None
    title: str | None = Field(None, max_length=120)


def _add_item(cur, folder, kind, ref, title, ctx):
    rid = pid = mid = url = None
    if kind == "report":
        cur.execute("select id from rpt.report where code = %s", (ref,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(422, "No report with code %s." % ref)
        rid = r["id"]
    elif kind == "master":
        cur.execute("select id from mst.master where code = %s", (ref,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(422, "No master with code %s." % ref)
        mid = r["id"]
    elif kind == "portal":
        cur.execute("select id from portal.portal where id::text = %s or code = %s", (ref, ref))
        r = cur.fetchone()
        if not r:
            raise HTTPException(422, "No portal %s." % ref)
        pid = r["id"]
    else:
        if not (ref or "").startswith(("http://", "https://")):
            raise HTTPException(422, "Links must start with http:// or https://.")
        url = ref
    cur.execute("select coalesce(max(sort_order), 0) + 10 n from nav.item where folder_id = %s", (folder["id"],))
    cur.execute("insert into nav.item (folder_id, kind, report_id, portal_id, master_id, url, title, sort_order, updated_by)"
                " values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                (folder["id"], kind, rid, pid, mid, url, title, cur.fetchone()["n"], getattr(ctx, "user_id", None)))
    return cur.fetchone()["id"]


def _item_row(cur, iid, section):
    cur.execute("select i.*, f.tree, f.section from nav.item i join nav.folder f on f.id = i.folder_id where i.id = %s", (iid,))
    it = cur.fetchone()
    if not it or it["section"] != section:
        raise HTTPException(404, "Item not found.")
    return it


class ItemUpdate(BaseModel):
    title: str | None = Field(None, max_length=120)
    sort_order: int | None = None
    folder_id: str | None = None


# ---------------------------------------------------------------- paste a design
class ImportIn(BaseModel):
    tree: str
    target_folder_id: str | None = None
    mode: str = Field("merge", pattern=r"^(merge|replace)$")
    text: str = Field(min_length=1, max_length=200_000)


def _resolve(cur, body: ImportIn, section="reports"):
    """Parse and annotate every node with new / existing / error against the database."""
    nodes = rimp.merge_duplicates(rimp.parse(body.text))
    target = _folder(cur, body.target_folder_id, section=section) if body.target_folder_id else None
    if target and target["tree"] != body.tree:
        raise HTTPException(422, "Target folder is in another tree.")
    cur.execute("select code from rpt.report")
    reports = {r["code"].lower() for r in cur.fetchall()}
    cur.execute("select code from mst.master")
    masters = {r["code"].lower() for r in cur.fetchall()}
    cur.execute("select code from portal.portal")
    portals = {r["code"].lower() for r in cur.fetchall()}
    counts = {"new_folders": 0, "existing_folders": 0, "items": 0, "errors": 0}

    def annotate(ns, parent_id, parent_known, at_root):
        out = []
        for n in ns:
            d = {"line": n.line, "type": n.type, "name": n.name, "ref": None, "status": "new", "message": n.error,
                 "children": []}
            if n.type == "item":
                d["ref"] = "%s:%s" % (n.ref_kind, n.ref) if n.ref_kind in ("report", "portal", "master") else n.ref
                if not n.error and n.ref_kind == "report" and n.ref.lower() not in reports:
                    d["message"] = "No report with code %s in the catalog." % n.ref
                if not n.error and n.ref_kind == "master" and n.ref.lower() not in masters:
                    d["message"] = "No master with code %s in the catalog." % n.ref
                if not n.error and n.ref_kind == "portal" and n.ref.lower() not in portals:
                    d["message"] = "No portal with code %s." % n.ref
                if at_root and not d["message"]:
                    d["message"] = "Items need a folder above them (or choose a target folder)."
                d["status"] = "error" if d["message"] else "item"
                counts["errors" if d["message"] else "items"] += 1
            else:
                fid = None
                if parent_known and not n.error and not (body.mode == "replace" and parent_id == (target["id"] if target else None)):
                    cur.execute("select id from nav.folder where section = %s and tree = %s and parent_id is not distinct from %s"
                                " and lower(name) = lower(%s) and not is_archived", (section, body.tree, parent_id, n.name))
                    row = cur.fetchone()
                    fid = row["id"] if row else None
                d["status"] = "error" if n.error else ("existing" if fid else "new")
                counts["errors" if n.error else ("existing_folders" if fid else "new_folders")] += 1
                d["children"] = annotate(n.children, fid, fid is not None, False)
                d["_fid"] = fid
            out.append(d)
        return out
    tree_out = annotate(nodes, target["id"] if target else None, True, target is None)
    return tree_out, counts, target


def _strip(ns):
    return [{k: (v if k != "children" else _strip(v)) for k, v in n.items() if k != "_fid"} for n in ns]


def _import_apply(body: ImportIn, request: Request, ctx: Ctx, section: str):
    _check_tree(body.tree)
    _need_edit(ctx, body.tree, section)
    with db.tx() as cur:
        nodes, counts, target = _resolve(cur, body, section)
        if counts["errors"]:
            raise HTTPException(422, "Fix the %d error line(s) shown in the preview first." % counts["errors"])
        archived = 0
        if body.mode == "replace":
            if target:
                cur.execute("update nav.folder set is_archived = true, updated_by = %s where path like %s and id <> %s"
                            " and not is_archived", (ctx.user_id, target["path"] + "%", target["id"]))
                archived = cur.rowcount
                cur.execute("delete from nav.item where folder_id = %s", (target["id"],))
            else:
                cur.execute("update nav.folder set is_archived = true, updated_by = %s where section = %s and tree = %s"
                            " and not is_archived", (ctx.user_id, section, body.tree))
                archived = cur.rowcount

        def build(ns, parent):
            for n in ns:
                if n["type"] == "folder":
                    f = _folder(cur, n["_fid"]) if n["_fid"] else _create_folder(cur, body.tree, parent, n["name"], ctx, section)
                    build(n["children"], f)
                else:
                    kind, _, ref = (n["ref"] or "").partition(":")
                    if kind in ("report", "portal", "master"):
                        _add_item(cur, parent, kind, ref, n["name"], ctx)
                    else:
                        _add_item(cur, parent, "link", n["ref"], n["name"], ctx)
        build(nodes, target)
        cur.execute("insert into nav.import (section, tree, target_folder_id, mode, source_text, summary, applied_by)"
                    " values (%s,%s,%s,%s,%s,%s,%s) returning id",
                    (section, body.tree, body.target_folder_id, body.mode, body.text,
                     json.dumps({**counts, "archived": archived}), ctx.user_id))
        iid = cur.fetchone()["id"]
        audit.action(cur, ctx, section + ".import", "tree", body.tree,
                     {"import_id": iid, "mode": body.mode, "target_folder_id": body.target_folder_id, **counts,
                      "archived": archived}, req=request)
    return {"data": {"import_id": iid, "counts": {**counts, "archived": archived}}}


# ---------------------------------------------------------------- routes, once per section
def _tree_routes(section: str):
    """Library and folder-editing routes under /api/v1/<section>/... Audit actions are prefixed with the
    section ('reports.folder.create', 'masters.folder.create'), matching what the report library logged."""
    VIEW = require(section + ".view")
    pre = "/" + section

    @router.get(pre + "/trees")
    def trees(ctx: Ctx = Depends(VIEW)):
        return _trees(ctx, section)

    @router.put(pre + "/trees/{tree}")
    def rename_tree(tree: str, body: TreeNameIn, request: Request, ctx: Ctx = Depends(VIEW)):
        return _rename_tree(tree, body, ctx, section, request)

    @router.post(pre + "/trees")
    def create_tree(body: TreeCreateIn, request: Request, ctx: Ctx = Depends(VIEW)):
        # New top-level tree/section (custom:<slug>), portal-admin only.
        _need_folder_admin(ctx, section)
        with db.tx() as cur:
            return _create_tree(cur, body, ctx, section, request)

    @router.get(pre + "/tree")
    def tree(tree: str = "central", ctx: Ctx = Depends(VIEW)):
        return _tree(tree, ctx, section)

    @router.get(pre + "/search")
    def search(q: str, ctx: Ctx = Depends(VIEW)):
        return _search(q, ctx, section)

    @router.post(pre + "/folders")
    def create_folder(body: FolderIn, request: Request, ctx: Ctx = Depends(VIEW)):
        _check_tree(body.tree)
        _need_folder_admin(ctx, section)
        _need_edit(ctx, body.tree, section)
        with db.tx() as cur:
            parent = _folder(cur, body.parent_id, section=section) if body.parent_id else None
            if parent and parent["tree"] != body.tree:
                raise HTTPException(422, "Parent folder is in another tree.")
            f = _create_folder(cur, body.tree, parent, body.name, ctx, section, name_hi=body.name_hi,
                               description=body.description, icon=body.icon, audience_roles=body.audience_roles)
            audit.action(cur, ctx, section + ".folder.create", "folder", f["id"],
                         {"tree": body.tree, "name": body.name, "parent_id": body.parent_id}, req=request)
        return {"data": {"id": str(f["id"]), "name": f["name"]}}

    @router.put(pre + "/folders/{fid}")
    def update_folder(fid: str, body: FolderUpdate, request: Request, ctx: Ctx = Depends(VIEW)):
        with db.tx() as cur:
            f = _folder(cur, fid, lock=True, section=section)
            _need_edit(ctx, f["tree"], section)
            new = body.model_dump(exclude_none=True)
            if "name" in new and new["name"].lower() != f["name"].lower():
                cur.execute("select 1 from nav.folder where section = %s and tree = %s and parent_id is not distinct from %s"
                            " and lower(name) = lower(%s) and not is_archived and id <> %s",
                            (section, f["tree"], f["parent_id"], new["name"], fid))
                if cur.fetchone():
                    raise HTTPException(409, "A folder with that name already exists here.")
            if new:
                cur.execute("update nav.folder set %s, updated_at = now(), updated_by = %%s where id = %%s"
                            % ", ".join("%s = %%s" % k for k in new), [*new.values(), ctx.user_id, fid])
            audit.action(cur, ctx, section + ".folder.update", "folder", fid,
                         {"changes": {k: {"from": f[k], "to": v} for k, v in new.items() if f[k] != v}}, req=request)
        return {"ok": True}

    @router.post(pre + "/folders/{fid}/move")
    def move_folder(fid: str, body: MoveIn, request: Request, ctx: Ctx = Depends(VIEW)):
        with db.tx() as cur:
            f = _folder(cur, fid, lock=True, section=section)
            _need_edit(ctx, f["tree"], section)
            newp = _folder(cur, body.parent_id, section=section) if body.parent_id else None
            if newp and (newp["tree"] != f["tree"]):
                raise HTTPException(422, "Folders can only move within their tree.")
            if newp and newp["path"].startswith(f["path"]):
                raise HTTPException(409, "A folder cannot move inside itself.")
            if str(newp["id"] if newp else None) != str(f["parent_id"]):
                cur.execute("select 1 from nav.folder where section = %s and tree = %s and parent_id is not distinct from %s"
                            " and lower(name) = lower(%s) and not is_archived and id <> %s",
                            (section, f["tree"], newp["id"] if newp else None, f["name"], fid))
                if cur.fetchone():
                    raise HTTPException(409, "The destination already has a folder named '%s'." % f["name"])
            new_path = (newp["path"] if newp else "/") + str(f["id"]) + "/"
            cur.execute("update nav.folder set path = %s || substr(path, length(%s) + 1) where path like %s",
                        (new_path, f["path"], f["path"] + "%"))
            cur.execute("update nav.folder set parent_id = %s, updated_at = now(), updated_by = %s where id = %s",
                        (newp["id"] if newp else None, ctx.user_id, fid))
            _place(cur, "nav.folder", _sibling_scope(f), newp["id"] if newp else None, fid, body.sort_order)
            audit.action(cur, ctx, section + ".folder.move", "folder", fid,
                         {"from_parent": f["parent_id"], "to_parent": body.parent_id, "sort_order": body.sort_order},
                         req=request)
        return {"ok": True}

    @router.delete(pre + "/folders/{fid}")
    def archive_folder(fid: str, request: Request, ctx: Ctx = Depends(VIEW)):
        with db.tx() as cur:
            f = _folder(cur, fid, lock=True, section=section)
            _need_edit(ctx, f["tree"], section)
            cur.execute("update nav.folder set is_archived = true, updated_at = now(), updated_by = %s where path like %s"
                        " and not is_archived", (ctx.user_id, f["path"] + "%"))
            n = cur.rowcount
            audit.action(cur, ctx, section + ".folder.archive", "folder", fid, {"name": f["name"], "folders_archived": n},
                         req=request)
        return {"ok": True, "archived": n}

    @router.post(pre + "/folders/{fid}/items")
    def add_item(fid: str, body: ItemIn, request: Request, ctx: Ctx = Depends(VIEW)):
        with db.tx() as cur:
            f = _folder(cur, fid, section=section)
            _need_edit(ctx, f["tree"], section)
            ref = {"report": body.report_code, "master": body.master_code, "portal": body.portal_id, "link": body.url}[body.kind]
            iid = _add_item(cur, f, body.kind, ref, body.title, ctx)
            audit.action(cur, ctx, section + ".item.add", "item", iid, {"folder_id": fid, "kind": body.kind, "ref": ref},
                         req=request)
        return {"data": {"id": str(iid)}}

    @router.put(pre + "/items/{iid}")
    def update_item(iid: str, body: ItemUpdate, request: Request, ctx: Ctx = Depends(VIEW)):
        with db.tx() as cur:
            it = _item_row(cur, iid, section)
            _need_edit(ctx, it["tree"], section)
            if body.folder_id:
                dest = _folder(cur, body.folder_id, section=section)
                _need_edit(ctx, dest["tree"], section)
            new = body.model_dump(exclude_none=True)
            pos = new.pop("sort_order", None)
            if new:
                cur.execute("update nav.item set %s, updated_by = %%s where id = %%s" % ", ".join("%s = %%s" % k for k in new),
                            [*new.values(), ctx.user_id, iid])
            if pos is not None or "folder_id" in new:
                _place(cur, "nav.item", "folder_id", new.get("folder_id") or it["folder_id"], iid, pos)
            if pos is not None:
                new["position"] = pos
            audit.action(cur, ctx, section + ".item.update", "item", iid, new, req=request)
        return {"ok": True}

    @router.delete(pre + "/items/{iid}")
    def delete_item(iid: str, request: Request, ctx: Ctx = Depends(VIEW)):
        with db.tx() as cur:
            it = _item_row(cur, iid, section)
            _need_edit(ctx, it["tree"], section)
            cur.execute("delete from nav.item where id = %s", (iid,))
            audit.action(cur, ctx, section + ".item.remove", "item", iid,
                         {"folder_id": it["folder_id"], "kind": it["kind"], "report_id": it["report_id"],
                          "master_id": it["master_id"], "portal_id": it["portal_id"], "url": it["url"]}, req=request)
        return {"ok": True}

    @router.post(pre + "/import/preview")
    def import_preview(body: ImportIn, ctx: Ctx = Depends(VIEW)):
        _check_tree(body.tree)
        _need_edit(ctx, body.tree, section)
        with db.tx() as cur:
            nodes, counts, _ = _resolve(cur, body, section)
        return {"data": {"nodes": _strip(nodes), "counts": counts}}

    @router.post(pre + "/import/apply")
    def import_apply(body: ImportIn, request: Request, ctx: Ctx = Depends(VIEW)):
        return _import_apply(body, request, ctx, section)


for _section in SECTIONS:
    _tree_routes(_section)


# ---------------------------------------------------------------- catalog and viewer
def _report(cur, code, ctx):
    cur.execute("select * from rpt.report where code = %s", (code,))
    r = cur.fetchone()
    if not r or (r["status"] == "draft" and not ctx.can("reports.manage")) or not _roles_ok(r["audience_roles"], ctx):
        raise HTTPException(404, "Report not found.")
    return r


def _shape_report(r):
    return {k: (str(v) if k in ("id", "updated_by") and v else v) for k, v in r.items()}


@router.get("/reports/catalog")
def catalog(q: str = "", kind: str = "", layer: str = "", owner_dept: str = "", ctx: Ctx = Depends(VIEW)):
    with db.tx() as cur:
        cur.execute("select * from rpt.report where (%s = '' or lower(name) like %s or lower(code) like %s)"
                    " and (%s = '' or kind = %s) and (%s = '' or layer = %s) and (%s = '' or owner_dept = %s)"
                    " and status <> 'deprecated' order by layer, owner_dept nulls first, name limit 500",
                    (q, "%" + q.lower() + "%", "%" + q.lower() + "%", kind, kind, layer, layer, owner_dept, owner_dept))
        rows = [r for r in cur.fetchall() if _roles_ok(r["audience_roles"], ctx)
                and (r["status"] == "live" or ctx.can("reports.manage"))]
    return envelope([_shape_report(r) for r in rows])


@router.get("/reports/{code}")
def report_detail(code: str, ctx: Ctx = Depends(VIEW)):
    with db.tx() as cur:
        r = _report(cur, code, ctx)
        cur.execute("select f.tree, f.id, f.path from nav.item i join nav.folder f on f.id = i.folder_id"
                    " where i.report_id = %s and not f.is_archived", (r["id"],))
        places = []
        for p in cur.fetchall():
            if not can_see_tree(ctx, p["tree"]):
                continue
            ids = [x for x in p["path"].strip("/").split("/") if x]
            cur.execute("select id, name from nav.folder where id = any(%s::uuid[])", (ids,))
            nm = {str(x["id"]): x["name"] for x in cur.fetchall()}
            places.append({"tree": p["tree"], "folder_id": str(p["id"]), "path_names": [nm.get(i, "?") for i in ids]})
        out = _shape_report(r)
        out["placements"] = places
        if r["kind"] == "table" and r["query_code"]:
            cur.execute("select params, name from data.query_def where code = %s", (r["query_code"],))
            q = cur.fetchone()
            kinds = {"int": "number", "date": "date", "str": "text"}
            out["params"] = [{"name": k, "label": v.get("label") or k.replace("_", " ").capitalize(),
                              "type": "select" if v.get("choices") else kinds.get(v.get("type", "str"), "text"),
                              "options": v.get("choices"), "default": v.get("default"), "required": bool(v.get("required"))}
                             for k, v in ((q or {}).get("params") or {}).items()]
        if r["kind"] == "control_tower":
            out["ct_status"] = _ct_report(cur, r["ct_report_id"])
    return {"data": out}


def _ct_report(cur, rid):
    cur.execute("select payload->'reports' reps, taken_at from ops.ct_snapshot where error is null order by id desc limit 1")
    s = cur.fetchone()
    if not s:
        return None
    for rep in s["reps"] or []:
        if rep.get("id") == rid:
            return {k: rep.get(k) for k in ("status", "reasons", "freshness", "last_run", "next_run", "owner",
                                            "schedule_note", "department", "max_age_hours")} | {"snapshot_at": s["taken_at"].isoformat()}
    return None


@router.post("/reports/{code}/open")
def open_report(code: str, request: Request, ctx: Ctx = Depends(VIEW)):
    with db.tx() as cur:
        r = _report(cur, code, ctx)
        url = r["url"]
        if r["kind"] == "portal":
            cur.execute("select * from portal.portal where code = %s", (r["portal_code"],))
            p = cur.fetchone()
            if not p or not portal_visible(p, ctx) or p["is_placeholder"]:
                raise HTTPException(409, "The portal for this report is not available to you yet.")
            url = p["url"]
        elif r["kind"] == "control_tower":
            url = "http://DataCenter.v2rd.com:8874"
        if not url:
            raise HTTPException(409, "This report has no link to open.")
        audit.action(cur, ctx, "report.open", "report", code, {"url": url}, req=request)
    return {"url": url}


@router.get("/reports/{code}/file")
def report_file(code: str, request: Request, ctx: Ctx = Depends(VIEW)):
    with db.tx() as cur:
        r = _report(cur, code, ctx)
    if r["kind"] != "file" or not r["file_path"]:
        raise HTTPException(409, "This report is not a file.")
    root = os.path.realpath(settings().files_root)
    path = os.path.realpath(os.path.join(root, r["file_path"]))
    if not path.startswith(root + os.sep):
        raise HTTPException(403, "File is outside the allowed folder.")
    if not os.path.isfile(path):
        raise HTTPException(404, "The file is not there yet. It may not have been generated today.")
    audit.action_now(ctx, "report.download", "report", code, {"file": r["file_path"], "bytes": os.path.getsize(path)}, req=request)
    return FileResponse(path, filename=os.path.basename(path))


@router.get("/reports/{code}/data")
def report_data(code: str, request: Request, sort: str = "", dir: str = "asc", cursor: str = "", limit: int = 100,
                format: str = "", ctx: Ctx = Depends(VIEW)):
    with db.tx() as cur:
        r = _report(cur, code, ctx)
        if r["kind"] != "table" or not r["query_code"]:
            raise HTTPException(409, "This report does not open as a table.")
        cur.execute("select * from data.query_def where code = %s and enabled", (r["query_code"],))
        qdef = cur.fetchone()
    if not qdef:
        raise HTTPException(409, "This report's query is disabled.")
    params = {k: v for k, v in request.query_params.items() if k not in ("sort", "dir", "cursor", "limit", "format")}
    try:
        res = gateway.run(qdef, params, ctx.scope, ctx.user_id)
    except gateway.GatewayError as e:
        raise HTTPException(e.status, str(e))
    cols, rows = res["columns"], res["rows"]
    if sort and sort in cols:
        k = cols.index(sort)
        rows = sorted(rows, key=lambda x: (x[k] is None, x[k]), reverse=(dir == "desc"))
    if format == "csv":
        audit.action_now(ctx, "report.export", "report", code, {"rows": len(rows), "params": params, "format": "csv"},
                         req=request)

        def gen():
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(cols)
            for row in rows:
                w.writerow(row)
                if buf.tell() > 65536:
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate()
            yield buf.getvalue()
        return StreamingResponse(gen(), media_type="text/csv",
                                 headers={"Content-Disposition": 'attachment; filename="%s.csv"' % code})
    off = int(cursor) if cursor.isdigit() else 0
    limit = max(1, min(limit, 500))
    page = rows[off:off + limit]
    nxt = str(off + limit) if off + limit < len(rows) else None
    return envelope([dict(zip(cols, x)) for x in page], as_of=res["as_of"],
                    freshness="ok", dataset=r["query_code"], scope=ctx.scope_label,
                    columns=[{"name": c} for c in cols], row_count=len(rows), cache_hit=res["cache_hit"], next_cursor=nxt)


# ---------------------------------------------------------------- admin catalog
class ReportIn(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,60}$")
    name: str = Field(min_length=2, max_length=160)
    description: str = ""
    layer: str = Field("department", pattern=r"^(central|department)$")
    owner_dept: str | None = None
    owner_name: str | None = None
    kind: str = Field(pattern=r"^(table|link|portal|file|control_tower)$")
    query_code: str | None = None
    url: str | None = None
    portal_code: str | None = None
    file_path: str | None = None
    ct_report_id: str | None = None
    status: str = Field("live", pattern=r"^(draft|live|deprecated)$")
    audience_roles: list[str] = []
    tags: list[str] = []


RFIELDS = list(ReportIn.model_fields)


def _validate_report(cur, b: ReportIn):
    need = {"table": "query_code", "link": "url", "portal": "portal_code", "file": "file_path", "control_tower": "ct_report_id"}
    if not getattr(b, need[b.kind]):
        raise HTTPException(422, "A %s report needs %s." % (b.kind, need[b.kind]))
    if b.kind == "table":
        cur.execute("select 1 from data.query_def where code = %s", (b.query_code,))
        if not cur.fetchone():
            raise HTTPException(422, "No query definition %s." % b.query_code)
    if b.kind == "link" and not b.url.startswith(("http://", "https://")):
        raise HTTPException(422, "Links must start with http:// or https://.")
    if b.kind == "file" and (".." in b.file_path or os.path.isabs(b.file_path)):
        raise HTTPException(422, "File path must be relative to the reports folder.")


@router.post("/admin/reports")
def create_report(body: ReportIn, request: Request, ctx: Ctx = Depends(require("reports.manage"))):
    with db.tx() as cur:
        _validate_report(cur, body)
        cur.execute("select 1 from rpt.report where code = %s", (body.code,))
        if cur.fetchone():
            raise HTTPException(409, "Report code %s already exists." % body.code)
        cols = RFIELDS + ["updated_by"]
        cur.execute("insert into rpt.report (%s) values (%s)" % (", ".join(cols), ", ".join(["%s"] * len(cols))),
                    [getattr(body, f) for f in RFIELDS] + [ctx.user_id])
        audit.action(cur, ctx, "report.create", "report", body.code, body.model_dump(), req=request)
    return {"ok": True}


@router.put("/admin/reports/{code}")
def update_report(code: str, body: ReportIn, request: Request, ctx: Ctx = Depends(require("reports.manage"))):
    with db.tx() as cur:
        cur.execute("select * from rpt.report where code = %s for update", (code,))
        before = cur.fetchone()
        if not before:
            raise HTTPException(404, "Report not found.")
        _validate_report(cur, body)
        new = body.model_dump()
        cur.execute("update rpt.report set %s, updated_at = now(), updated_by = %%s where code = %%s"
                    % ", ".join("%s = %%s" % f for f in RFIELDS), [new[f] for f in RFIELDS] + [ctx.user_id, code])
        audit.action(cur, ctx, "report.update", "report", code,
                     {"changes": {k: {"from": before[k], "to": v} for k, v in new.items() if before.get(k) != v}}, req=request)
    return {"ok": True}


def sync_ct_reports(cur, ct_reports: list[dict]) -> int:
    """Register every Report Portal catalog entry as a control_tower report and, the first time,
    file it in the Scheduled Reports section under its department (sched_folder)."""
    made = 0
    for rep in ct_reports:
        code = "ct-%s" % rep["id"]
        dept = (rep.get("department") or "").upper()
        cur.execute("insert into rpt.report (code, name, description, layer, owner_dept, owner_name, kind, ct_report_id)"
                    " values (%s,%s,%s,'department',%s,%s,'control_tower',%s)"
                    " on conflict (code) do update set name = excluded.name, description = excluded.description,"
                    " owner_dept = excluded.owner_dept, owner_name = excluded.owner_name returning id, (xmax = 0) inserted",
                    (code, rep["name"], rep.get("description") or "", dept or None, rep.get("owner") or None, rep["id"]))
        row = cur.fetchone()
        if not row["inserted"] or not dept:
            continue
        cur.execute("select 1 from iam.org_node where node_type = 'dept' and code = %s", (dept,))
        if not cur.fetchone():
            continue
        from .sched_folder import scheduled_folder
        fid = scheduled_folder(cur, "dept:" + dept, rep["name"])
        cur.execute("insert into nav.item (folder_id, kind, report_id) values (%s,'report',%s)", (fid, row["id"]))
        made += 1
    return made

