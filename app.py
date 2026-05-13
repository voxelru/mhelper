from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, func, text
from sqlalchemy.orm import joinedload


db = SQLAlchemy()

TaskStatus = Literal["planned", "in_progress", "done", "archived"]


class Group(db.Model):
    __tablename__ = "groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)

    tasks = db.relationship("Task", back_populates="group", cascade="all,delete", passive_deletes=True)


class Task(db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(240), nullable=False)
    description = db.Column(db.Text, nullable=True)
    link = db.Column(db.String(2000), nullable=True)
    assignee = db.Column(db.String(120), nullable=True)
    due_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="planned")
    priority = db.Column(db.Integer, nullable=False, default=4)

    group_id = db.Column(db.Integer, db.ForeignKey("groups.id", ondelete="SET NULL"), nullable=True)
    group = db.relationship("Group", back_populates="tasks")

    comments = db.relationship(
        "Comment",
        back_populates="task",
        cascade="all,delete-orphan",
        order_by="Comment.created_at.asc()",
    )

    __table_args__ = (
        CheckConstraint("status IN ('planned','in_progress','done','archived')", name="ck_tasks_status"),
        CheckConstraint("priority IN (0,1,2,3,4)", name="ck_tasks_priority"),
    )


class Comment(db.Model):
    __tablename__ = "comments"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    body = db.Column(db.Text, nullable=False)
    resolved = db.Column(db.Boolean, nullable=False, default=False)

    task = db.relationship("Task", back_populates="comments")


def _parse_optional_date(raw: str | None) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _parse_task_form(form: Any) -> tuple[str | None, dict[str, Any]]:
    """Returns (error_message or None, fields dict)."""
    title = (form.get("title") or "").strip()
    if not title:
        return "Укажите название задачи.", {}

    description = (form.get("description") or "").strip() or None
    link = (form.get("link") or "").strip() or None
    assignee = (form.get("assignee") or "").strip() or None
    group_id = form.get("group_id", type=int)
    if group_id is not None and group_id <= 0:
        group_id = None

    status = (form.get("status") or "").strip()
    if status not in ("planned", "in_progress", "done", "archived"):
        status = None

    due = _parse_optional_date(form.get("due_date"))
    priority_raw = form.get("priority")
    try:
        priority = int(priority_raw) if priority_raw is not None and str(priority_raw).strip() != "" else 4
    except (TypeError, ValueError):
        priority = 4
    if priority not in (0, 1, 2, 3, 4):
        priority = 4

    return None, {
        "title": title,
        "description": description,
        "link": link,
        "assignee": assignee,
        "due_date": due,
        "group_id": group_id if group_id else None,
        "status": status,
        "priority": priority,
    }


def _parse_tasks_table_request(req: Any) -> dict[str, Any]:
    """Shared query/sort params for /tasks and /comments list views."""
    q = (req.args.get("q") or "").strip()
    status_fs = [s.strip() for s in req.args.getlist("status") if (s or "").strip()]
    status_fs = [s for s in status_fs if s in ("planned", "in_progress", "done", "archived")]

    priority_fs_raw = req.args.getlist("priority")
    priority_fs: list[int] = []
    for p in priority_fs_raw:
        try:
            pi = int(str(p).strip())
        except (TypeError, ValueError):
            continue
        if pi in (0, 1, 2, 3, 4):
            priority_fs.append(pi)
    priority_fs = sorted(set(priority_fs))

    group_id_fs_raw = req.args.getlist("group_id")
    group_id_fs: list[int] = []
    for g in group_id_fs_raw:
        try:
            gi = int(str(g).strip())
        except (TypeError, ValueError):
            continue
        if gi > 0:
            group_id_fs.append(gi)
    group_id_fs = sorted(set(group_id_fs))
    assignee_f = (req.args.get("assignee") or "").strip()
    due_from = _parse_optional_date(req.args.get("due_from"))
    due_to = _parse_optional_date(req.args.get("due_to"))

    sort = (req.args.get("sort") or "id").strip()
    order = (req.args.get("order") or "desc").strip().lower()
    if sort not in ("id", "title", "status", "priority", "group", "assignee", "due_date"):
        sort = "id"
    if order not in ("asc", "desc"):
        order = "desc"

    return {
        "q": q,
        "status_fs": status_fs,
        "priority_fs": priority_fs,
        "group_id_fs": group_id_fs,
        "assignee_f": assignee_f,
        "due_from": due_from,
        "due_to": due_to,
        "due_from_raw": req.args.get("due_from") or "",
        "due_to_raw": req.args.get("due_to") or "",
        "sort": sort,
        "order": order,
    }


def _apply_task_row_filters(query: Any, fp: dict[str, Any]) -> Any:
    if fp["q"]:
        query = query.filter(Task.title.ilike(f"%{fp['q']}%"))
    if fp["status_fs"]:
        query = query.filter(Task.status.in_(fp["status_fs"]))
    if fp["priority_fs"]:
        query = query.filter(Task.priority.in_(fp["priority_fs"]))
    if fp["group_id_fs"]:
        query = query.filter(Task.group_id.in_(fp["group_id_fs"]))
    if fp["assignee_f"]:
        query = query.filter(Task.assignee.ilike(f"%{fp['assignee_f']}%"))
    if fp["due_from"] is not None:
        query = query.filter(Task.due_date.isnot(None), Task.due_date >= fp["due_from"])
    if fp["due_to"] is not None:
        query = query.filter(Task.due_date.isnot(None), Task.due_date <= fp["due_to"])
    return query


def _tasks_table_sort_column(sort: str) -> Any:
    if sort == "group":
        return Group.name
    if sort == "priority":
        return Task.priority
    if sort == "id":
        return Task.id
    if sort == "title":
        return Task.title
    if sort == "status":
        return Task.status
    if sort == "assignee":
        return Task.assignee
    return Task.due_date


def _filter_url_args(fp: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if fp["q"]:
        out["q"] = fp["q"]
    if fp["status_fs"]:
        out["status"] = fp["status_fs"]
    if fp["priority_fs"]:
        out["priority"] = fp["priority_fs"]
    if fp["group_id_fs"]:
        out["group_id"] = fp["group_id_fs"]
    if fp["assignee_f"]:
        out["assignee"] = fp["assignee_f"]
    if fp["due_from_raw"]:
        out["due_from"] = fp["due_from_raw"]
    if fp["due_to_raw"]:
        out["due_to"] = fp["due_to_raw"]
    return out


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///mhelper.sqlite3"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    with app.app_context():
        db.create_all()
        with db.engine.begin() as conn:
            cols = conn.execute(text("PRAGMA table_info(tasks)")).fetchall()
            col_names = {row[1] for row in cols}
            if "priority" not in col_names:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 4"))
            if "description" not in col_names:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN description TEXT"))

            # Expand tasks.status CHECK constraint to include 'archived' (SQLite requires table rebuild).
            tasks_sql_row = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'")
            ).fetchone()
            tasks_sql = (tasks_sql_row[0] if tasks_sql_row else "") or ""
            if "ck_tasks_status" in tasks_sql and "archived" not in tasks_sql:
                conn.execute(text("PRAGMA foreign_keys=OFF"))
                conn.execute(
                    text(
                        """
CREATE TABLE tasks__new (
  id INTEGER NOT NULL,
  title VARCHAR(240) NOT NULL,
  description TEXT,
  link VARCHAR(2000),
  assignee VARCHAR(120),
  due_date DATE,
  status VARCHAR(20) NOT NULL DEFAULT 'planned',
  priority INTEGER NOT NULL DEFAULT 4,
  group_id INTEGER,
  PRIMARY KEY (id),
  CONSTRAINT ck_tasks_status CHECK (status IN ('planned','in_progress','done','archived')),
  CONSTRAINT ck_tasks_priority CHECK (priority IN (0,1,2,3,4)),
  FOREIGN KEY(group_id) REFERENCES groups (id) ON DELETE SET NULL
)
"""
                    )
                )
                conn.execute(
                    text(
                        """
INSERT INTO tasks__new (id, title, description, link, assignee, due_date, status, priority, group_id)
SELECT id, title, description, link, assignee, due_date, status, priority, group_id
FROM tasks
"""
                    )
                )
                conn.execute(text("DROP TABLE tasks"))
                conn.execute(text("ALTER TABLE tasks__new RENAME TO tasks"))
                conn.execute(text("PRAGMA foreign_keys=ON"))

            comment_cols = conn.execute(text("PRAGMA table_info(comments)")).fetchall()
            comment_col_names = {row[1] for row in comment_cols}
            if "resolved" not in comment_col_names:
                conn.execute(text("ALTER TABLE comments ADD COLUMN resolved INTEGER NOT NULL DEFAULT 0"))

    @app.get("/")
    def board():
        group_id = request.args.get("group_id", type=int)
        groups = Group.query.order_by(Group.name.asc()).all()

        base_query = (
            Task.query.options(joinedload(Task.group), joinedload(Task.comments))
            .filter(Task.status != "archived")
            .order_by(Task.due_date.is_(None), Task.due_date.asc(), Task.id.desc())
        )
        if group_id is not None:
            base_query = base_query.filter(Task.group_id == group_id)

        tasks = base_query.all()
        buckets: dict[str, list[Task]] = {"planned": [], "in_progress": [], "done": []}
        for t in tasks:
            buckets.setdefault(t.status, []).append(t)

        return render_template(
            "board.html",
            groups=groups,
            selected_group_id=group_id,
            planned=buckets["planned"],
            in_progress=buckets["in_progress"],
            done=buckets["done"],
            today=date.today(),
        )

    @app.post("/groups")
    def create_group():
        name = (request.form.get("name") or "").strip()
        if not name:
            return redirect(url_for("board"))

        existing = Group.query.filter(db.func.lower(Group.name) == name.lower()).first()
        if existing is None:
            db.session.add(Group(name=name))
            db.session.commit()
        return redirect(url_for("board"))

    @app.post("/tasks")
    def create_task():
        err, fields = _parse_task_form(request.form)
        if err:
            return redirect(url_for("board"))

        task = Task(
            title=fields["title"],
            link=fields["link"],
            assignee=fields["assignee"],
            due_date=fields["due_date"],
            status="planned",
            group_id=fields["group_id"],
            priority=fields["priority"],
        )
        db.session.add(task)
        db.session.commit()
        return redirect(url_for("board", group_id=request.args.get("group_id", type=int)))

    @app.get("/tasks")
    def tasks_list():
        fp = _parse_tasks_table_request(request)
        sort, order = fp["sort"], fp["order"]

        last_comment_ranked = (
            db.session.query(
                Comment.task_id.label("task_id"),
                Comment.created_at.label("created_at"),
                Comment.body.label("body"),
                func.row_number()
                .over(partition_by=Comment.task_id, order_by=Comment.created_at.desc())
                .label("rn"),
            )
        ).subquery()

        last_comment = (
            db.session.query(
                last_comment_ranked.c.task_id.label("task_id"),
                last_comment_ranked.c.created_at.label("created_at"),
                last_comment_ranked.c.body.label("body"),
            )
            .filter(last_comment_ranked.c.rn == 1)
            .subquery()
        )

        query = (
            Task.query.options(joinedload(Task.group))
            .outerjoin(Group, Task.group_id == Group.id)
            .outerjoin(last_comment, last_comment.c.task_id == Task.id)
            .add_columns(
                last_comment.c.created_at.label("last_comment_at"),
                last_comment.c.body.label("last_comment_body"),
            )
        )

        query = _apply_task_row_filters(query, fp)
        sort_col = _tasks_table_sort_column(sort)

        if order == "asc":
            query = query.order_by(sort_col.asc(), Task.id.asc())
        else:
            query = query.order_by(sort_col.desc(), Task.id.desc())

        rows = query.all()
        tasks = [
            {"task": t, "last_comment_at": last_at, "last_comment_body": last_body}
            for (t, last_at, last_body) in rows
        ]
        groups = Group.query.order_by(Group.name.asc()).all()

        filter_args = _filter_url_args(fp)

        sort_links: dict[str, str] = {}
        for field in ("id", "priority", "title", "status", "group", "assignee", "due_date"):
            next_order = "desc" if sort == field and order == "asc" else "asc"
            sort_links[field] = url_for("tasks_list", **filter_args, sort=field, order=next_order)

        return render_template(
            "tasks_list.html",
            tasks=tasks,
            groups=groups,
            filters={
                "q": fp["q"],
                "status": fp["status_fs"],
                "priority": fp["priority_fs"],
                "group_id": fp["group_id_fs"],
                "assignee": fp["assignee_f"],
                "due_from": fp["due_from_raw"],
                "due_to": fp["due_to_raw"],
            },
            list_sort=sort,
            list_order=order,
            sort_links=sort_links,
        )

    @app.get("/comments")
    def comments_list():
        fp = _parse_tasks_table_request(request)
        sort, order = fp["sort"], fp["order"]

        global_open = (
            db.session.query(func.count(Comment.id))
            .join(Task, Comment.task_id == Task.id)
            .filter(Comment.resolved.is_(False), Task.status != "archived")
            .scalar()
            or 0
        )

        query = (
            Comment.query.options(joinedload(Comment.task).joinedload(Task.group))
            .join(Task, Comment.task_id == Task.id)
            .outerjoin(Group, Task.group_id == Group.id)
            .filter(Comment.resolved.is_(False), Task.status != "archived")
        )
        query = _apply_task_row_filters(query, fp)
        sort_col = _tasks_table_sort_column(sort)
        if order == "asc":
            query = query.order_by(sort_col.asc(), Task.id.asc(), Comment.created_at.desc())
        else:
            query = query.order_by(sort_col.desc(), Task.id.desc(), Comment.created_at.desc())

        rows = query.all()
        groups = Group.query.order_by(Group.name.asc()).all()
        filter_args = _filter_url_args(fp)

        sort_links: dict[str, str] = {}
        for field in ("id", "priority", "title", "status", "group", "assignee", "due_date"):
            next_order = "desc" if sort == field and order == "asc" else "asc"
            sort_links[field] = url_for("comments_list", **filter_args, sort=field, order=next_order)

        empty_all_open = len(rows) == 0 and global_open == 0

        return render_template(
            "comments_list.html",
            comments=rows,
            groups=groups,
            filters={
                "q": fp["q"],
                "status": fp["status_fs"],
                "priority": fp["priority_fs"],
                "group_id": fp["group_id_fs"],
                "assignee": fp["assignee_f"],
                "due_from": fp["due_from_raw"],
                "due_to": fp["due_to_raw"],
            },
            list_sort=sort,
            list_order=order,
            sort_links=sort_links,
            empty_all_open=empty_all_open,
        )

    @app.get("/tasks/<int:task_id>")
    def task_detail(task_id: int):
        task = Task.query.options(joinedload(Task.group)).get(task_id)
        if task is None:
            abort(404)
        return render_template("task.html", task=task)

    @app.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
    def task_edit(task_id: int):
        task = Task.query.options(joinedload(Task.group)).get(task_id)
        if task is None:
            abort(404)
        groups = Group.query.order_by(Group.name.asc()).all()

        if request.method == "POST":
            err, fields = _parse_task_form(request.form)
            if err:
                return render_template(
                    "task_edit.html",
                    task=task,
                    groups=groups,
                    error=err,
                    edit_form=request.form.to_dict(flat=True),
                )

            if fields["status"] is None:
                fields["status"] = task.status

            task.title = fields["title"]
            task.description = fields["description"]
            task.link = fields["link"]
            task.assignee = fields["assignee"]
            task.due_date = fields["due_date"]
            task.group_id = fields["group_id"]
            task.status = fields["status"]
            task.priority = fields["priority"]
            db.session.commit()
            return redirect(url_for("task_detail", task_id=task.id))

        return render_template("task_edit.html", task=task, groups=groups, error=None, edit_form=None)

    @app.post("/tasks/<int:task_id>/comments")
    def add_comment(task_id: int):
        task = Task.query.get(task_id)
        if task is None:
            abort(404)
        body = (request.form.get("body") or "").strip()
        if body:
            db.session.add(Comment(task_id=task.id, body=body))
            db.session.commit()
        return redirect(url_for("task_detail", task_id=task.id))

    @app.post("/tasks/<int:task_id>/comments/<int:comment_id>")
    def update_comment(task_id: int, comment_id: int):
        comment = Comment.query.filter_by(id=comment_id, task_id=task_id).first()
        if comment is None:
            abort(404)

        # Update only fields present in the form to avoid unintended overwrites
        if "resolved_present" in request.form:
            resolved_values = request.form.getlist("resolved")
            comment.resolved = "1" in resolved_values
        if "body" in request.form:
            body = (request.form.get("body") or "").strip()
            if body:
                comment.body = body
        db.session.commit()
        return redirect(url_for("task_detail", task_id=task_id))

    @app.post("/api/tasks/<int:task_id>/move")
    def api_move_task(task_id: int):
        payload = request.get_json(silent=True) or {}
        status = payload.get("status")
        if status not in ("planned", "in_progress", "done"):
            return jsonify({"ok": False, "error": "Invalid status"}), 400

        task = Task.query.get(task_id)
        if task is None:
            return jsonify({"ok": False, "error": "Not found"}), 404

        task.status = status
        db.session.commit()
        return jsonify({"ok": True})

    @app.post("/api/tasks/<int:task_id>/comments/<int:comment_id>/resolved")
    def api_comment_resolved(task_id: int, comment_id: int):
        comment = Comment.query.filter_by(id=comment_id, task_id=task_id).first()
        if comment is None:
            return jsonify({"ok": False, "error": "Not found"}), 404

        payload = request.get_json(silent=True) or {}
        resolved = payload.get("resolved")
        if not isinstance(resolved, bool):
            return jsonify({"ok": False, "error": "Body must be JSON with boolean \"resolved\""}), 400

        comment.resolved = resolved
        db.session.commit()
        return jsonify({"ok": True})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
