from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, func, text
from sqlalchemy.orm import joinedload


db = SQLAlchemy()

TaskStatus = Literal["planned", "in_progress", "done"]


class Group(db.Model):
    __tablename__ = "groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)

    tasks = db.relationship("Task", back_populates="group", cascade="all,delete", passive_deletes=True)


class Task(db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(240), nullable=False)
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
        CheckConstraint("status IN ('planned','in_progress','done')", name="ck_tasks_status"),
        CheckConstraint("priority IN (0,1,2,3,4)", name="ck_tasks_priority"),
    )


class Comment(db.Model):
    __tablename__ = "comments"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    body = db.Column(db.Text, nullable=False)

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

    link = (form.get("link") or "").strip() or None
    assignee = (form.get("assignee") or "").strip() or None
    group_id = form.get("group_id", type=int)
    if group_id is not None and group_id <= 0:
        group_id = None

    status = (form.get("status") or "").strip()
    if status not in ("planned", "in_progress", "done"):
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
        "link": link,
        "assignee": assignee,
        "due_date": due,
        "group_id": group_id if group_id else None,
        "status": status,
        "priority": priority,
    }


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

    @app.get("/")
    def board():
        group_id = request.args.get("group_id", type=int)
        groups = Group.query.order_by(Group.name.asc()).all()

        base_query = Task.query.order_by(Task.due_date.is_(None), Task.due_date.asc(), Task.id.desc())
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
        q = (request.args.get("q") or "").strip()
        status_f = (request.args.get("status") or "").strip()
        priority_f = request.args.get("priority", type=int)
        group_id_f = request.args.get("group_id", type=int)
        assignee_f = (request.args.get("assignee") or "").strip()
        due_from = _parse_optional_date(request.args.get("due_from"))
        due_to = _parse_optional_date(request.args.get("due_to"))

        sort = (request.args.get("sort") or "id").strip()
        order = (request.args.get("order") or "desc").strip().lower()
        if sort not in ("id", "title", "status", "priority", "group", "assignee", "due_date"):
            sort = "id"
        if order not in ("asc", "desc"):
            order = "desc"

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

        if q:
            query = query.filter(Task.title.ilike(f"%{q}%"))
        if status_f in ("planned", "in_progress", "done"):
            query = query.filter(Task.status == status_f)
        if priority_f in (0, 1, 2, 3, 4):
            query = query.filter(Task.priority == priority_f)
        if group_id_f is not None:
            query = query.filter(Task.group_id == group_id_f)
        if assignee_f:
            query = query.filter(Task.assignee.ilike(f"%{assignee_f}%"))
        if due_from is not None:
            query = query.filter(Task.due_date.isnot(None), Task.due_date >= due_from)
        if due_to is not None:
            query = query.filter(Task.due_date.isnot(None), Task.due_date <= due_to)

        sort_col: Any
        if sort == "group":
            sort_col = Group.name
        elif sort == "priority":
            sort_col = Task.priority
        elif sort == "id":
            sort_col = Task.id
        elif sort == "title":
            sort_col = Task.title
        elif sort == "status":
            sort_col = Task.status
        elif sort == "assignee":
            sort_col = Task.assignee
        else:
            sort_col = Task.due_date

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

        filter_args: dict[str, Any] = {}
        if q:
            filter_args["q"] = q
        if status_f:
            filter_args["status"] = status_f
        if priority_f in (0, 1, 2, 3, 4):
            filter_args["priority"] = priority_f
        if group_id_f is not None:
            filter_args["group_id"] = group_id_f
        if assignee_f:
            filter_args["assignee"] = assignee_f
        if request.args.get("due_from"):
            filter_args["due_from"] = request.args.get("due_from")
        if request.args.get("due_to"):
            filter_args["due_to"] = request.args.get("due_to")

        sort_links: dict[str, str] = {}
        for field in ("id", "priority", "title", "status", "group", "assignee", "due_date"):
            next_order = "desc" if sort == field and order == "asc" else "asc"
            sort_links[field] = url_for("tasks_list", **filter_args, sort=field, order=next_order)

        return render_template(
            "tasks_list.html",
            tasks=tasks,
            groups=groups,
            filters={
                "q": q,
                "status": status_f,
                "priority": priority_f,
                "group_id": group_id_f,
                "assignee": assignee_f,
                "due_from": request.args.get("due_from") or "",
                "due_to": request.args.get("due_to") or "",
            },
            list_sort=sort,
            list_order=order,
            sort_links=sort_links,
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

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
