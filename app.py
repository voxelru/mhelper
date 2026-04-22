from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint


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
    )


class Comment(db.Model):
    __tablename__ = "comments"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    body = db.Column(db.Text, nullable=False)

    task = db.relationship("Task", back_populates="comments")


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///mhelper.sqlite3"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    with app.app_context():
        db.create_all()

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
        title = (request.form.get("title") or "").strip()
        if not title:
            return redirect(url_for("board"))

        link = (request.form.get("link") or "").strip() or None
        assignee = (request.form.get("assignee") or "").strip() or None
        group_id = request.form.get("group_id", type=int)
        due_raw = (request.form.get("due_date") or "").strip()
        due = None
        if due_raw:
            try:
                due = date.fromisoformat(due_raw)
            except ValueError:
                due = None

        task = Task(
            title=title,
            link=link,
            assignee=assignee,
            due_date=due,
            status="planned",
            group_id=group_id if group_id else None,
        )
        db.session.add(task)
        db.session.commit()
        return redirect(url_for("board", group_id=request.args.get("group_id", type=int)))

    @app.get("/tasks/<int:task_id>")
    def task_detail(task_id: int):
        task = Task.query.get(task_id)
        if task is None:
            abort(404)
        return render_template("task.html", task=task)

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
