import hashlib
import json
import os
import secrets
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

import smtplib
import sqlite3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from urllib.parse import quote

from fastapi import FastAPI, Request, Form, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
import uvicorn

DB_PATH = BASE_DIR / "database.db"
app = FastAPI()


@app.get("/health")
async def health():
    """배포 상태 확인용"""
    return {"status": "ok"}


_HASH_ITERATIONS = 100000


def _hash_password(plain: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt.encode(), _HASH_ITERATIONS)
    return f"{salt}:{h.hex()}"


def _verify_password(plain: str, stored: str) -> bool:
    if ":" not in stored:
        return plain == stored
    salt, hexdig = stored.split(":", 1)
    h = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt.encode(), _HASH_ITERATIONS)
    return h.hex() == hexdig

# 10종류 뱃지 아이콘 (1~10)
BADGE_ICONS = [
    ("🏆", "트로피"),
    ("🥇", "금메달"),
    ("⭐", "별"),
    ("🎯", "과녁"),
    ("💡", "전구"),
    ("🔥", "불꽃"),
    ("🌟", "빛나는 별"),
    ("✨", "반짝임"),
    ("🎖️", "훈장"),
    ("🏅", "메달"),
]


def _format_note_date(iso_str: str) -> str:
    """'2026-01-30 14:30:00' -> '2026년 01월 30일'"""
    if not iso_str:
        return ""
    try:
        dt = datetime.strptime(iso_str[:10], "%Y-%m-%d")
        return dt.strftime("%Y년 %m월 %d일")
    except Exception:
        return iso_str[:10] if iso_str else ""


templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["enumerate"] = enumerate
templates.env.globals["format_note_date"] = _format_note_date


def _conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS contribution (
                member TEXT NOT NULL, year TEXT NOT NULL, month INT NOT NULL, paid INT NOT NULL,
                PRIMARY KEY (member, year, month)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS member_notes (
                member TEXT PRIMARY KEY, note TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, done INT NOT NULL, audience TEXT DEFAULT 'all'
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS members (
                id TEXT PRIMARY KEY, password TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS partners (
                id TEXT PRIMARY KEY, password TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS backers (
                id TEXT PRIMARY KEY, password TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id TEXT PRIMARY KEY, password TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS member_badges (
                id INTEGER PRIMARY KEY, member_id TEXT NOT NULL,
                mission_name TEXT NOT NULL, icon_type INT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id TEXT PRIMARY KEY, password_hash TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_last_login (
                user_id TEXT PRIMARY KEY, last_login_at TEXT, last_login_ip TEXT
            )
        """)
        c.commit()
    try:
        with _conn() as c:
            c.execute("ALTER TABLE todos ADD COLUMN audience TEXT DEFAULT 'all'")
            c.commit()
    except sqlite3.OperationalError:
        pass
    try:
        with _conn() as c:
            c.execute("ALTER TABLE members ADD COLUMN sort_order INT DEFAULT 0")
            c.execute("""
                UPDATE members SET sort_order = (SELECT COUNT(*) FROM members m2 WHERE m2.id < members.id)
            """)
            c.commit()
    except sqlite3.OperationalError:
        pass
    try:
        with _conn() as c:
            c.execute("ALTER TABLE member_notes ADD COLUMN note_updated_at TEXT")
            c.commit()
    except sqlite3.OperationalError:
        pass
    try:
        with _conn() as c:
            c.execute("ALTER TABLE partners ADD COLUMN sort_order INT DEFAULT 0")
            c.execute("""
                UPDATE partners SET sort_order = (SELECT COUNT(*) FROM partners p2 WHERE p2.id < partners.id)
            """)
            c.commit()
    except sqlite3.OperationalError:
        pass
    try:
        with _conn() as c:
            c.execute("ALTER TABLE members ADD COLUMN equity TEXT DEFAULT ''")
            c.commit()
    except sqlite3.OperationalError:
        pass
    try:
        with _conn() as c:
            c.execute("ALTER TABLE partners ADD COLUMN equity TEXT DEFAULT ''")
            c.commit()
    except sqlite3.OperationalError:
        pass
    try:
        with _conn() as c:
            c.execute("ALTER TABLE todos ADD COLUMN sort_order INT DEFAULT 0")
            c.commit()
    except sqlite3.OperationalError:
        pass
    try:
        with _conn() as c:
            c.execute("ALTER TABLE todos ADD COLUMN detail TEXT DEFAULT ''")
            c.commit()
    except sqlite3.OperationalError:
        pass
    for tbl in ("backers", "customers"):
        try:
            with _conn() as c:
                c.execute(f"ALTER TABLE {tbl} ADD COLUMN sort_order INT DEFAULT 0")
                c.commit()
        except sqlite3.OperationalError:
            pass
        try:
            with _conn() as c:
                c.execute(f"ALTER TABLE {tbl} ADD COLUMN equity TEXT DEFAULT ''")
                c.commit()
        except sqlite3.OperationalError:
            pass
    _seed_members_partners()
    _seed_admin()


def load_contrib_from_db():
    out = {}
    with _conn() as c:
        for row in c.execute("SELECT member, year, month, paid FROM contribution ORDER BY member, year, month"):
            m, y, mn, paid = row
            if m not in out:
                out[m] = {}
            if y not in out[m]:
                out[m][y] = [False] * 12
            out[m][y][mn] = bool(paid)
    return out


def load_notes_from_db():
    out = {}
    dates = {}
    with _conn() as c:
        try:
            for row in c.execute("SELECT member, note, note_updated_at FROM member_notes"):
                out[row[0]] = row[1] or ""
                dates[row[0]] = row[2] if len(row) > 2 and row[2] else None
        except sqlite3.OperationalError:
            for row in c.execute("SELECT member, note FROM member_notes"):
                out[row[0]] = row[1] or ""
                dates[row[0]] = None
    return out, dates


def load_todos_from_db():
    out = []
    with _conn() as c:
        try:
            rows = c.execute("SELECT id, title, done, audience, sort_order, detail FROM todos ORDER BY sort_order ASC, id ASC")
        except sqlite3.OperationalError:
            try:
                rows = c.execute("SELECT id, title, done, audience, sort_order FROM todos ORDER BY sort_order ASC, id ASC")
            except sqlite3.OperationalError:
                try:
                    rows = c.execute("SELECT id, title, done, audience FROM todos ORDER BY id")
                except sqlite3.OperationalError:
                    rows = c.execute("SELECT id, title, done FROM todos ORDER BY id")
        for row in rows:
            r = list(row)
            if len(r) == 3:
                r.extend(["all", 0, ""])
            elif len(r) == 4:
                r.extend([0, ""])
            elif len(r) == 5:
                r.append("")
            out.append({
                "id": r[0], "title": r[1], "done": bool(r[2]),
                "audience": r[3] or "all",
                "sort_order": r[4] if len(r) > 4 and r[4] is not None else 0,
                "detail": (r[5] or "").strip() if len(r) > 5 else ""
            })
    return out


def get_todos_for_user(current_user):
    all_todos = load_todos_from_db()
    if current_user == "admin":
        return all_todos
    member_ids = {m["id"] for m in load_members_from_db()}
    partner_ids = {p["id"] for p in load_partners_from_db()}
    is_member = current_user in member_ids
    is_partner = current_user in partner_ids
    out = []
    for t in all_todos:
        aud = t.get("audience") or "all"
        if aud == "all":
            out.append(t)
        elif aud == "members" and is_member:
            out.append(t)
        elif aud == "partners" and is_partner:
            out.append(t)
        elif current_user in [x.strip() for x in aud.split(",") if x.strip()]:
            out.append(t)
    return out


def save_contrib_to_db(data):
    """data: {user_id: {year: [bool x 12]}}"""
    with _conn() as c:
        c.execute("DELETE FROM contribution")
        for uid, ys in data.items():
            for y, months in ys.items():
                if len(months) != 12:
                    continue
                for mn, paid in enumerate(months):
                    c.execute(
                        "INSERT INTO contribution (member, year, month, paid) VALUES (?,?,?,?)",
                        (uid, y, mn, 1 if paid else 0),
                    )
        c.commit()


def _merge_all_contrib():
    """Merge MEMBER, PARTNER, BACKER, CUSTOMER data for save."""
    return {**MEMBER_DATA, **PARTNER_DATA, **BACKER_DATA, **CUSTOMER_DATA}


def save_notes_to_db(notes):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as c:
        c.execute("DELETE FROM member_notes")
        try:
            for m, txt in notes.items():
                c.execute(
                    "INSERT INTO member_notes (member, note, note_updated_at) VALUES (?,?,?)",
                    (m, txt or "", now),
                )
        except sqlite3.OperationalError:
            for m, txt in notes.items():
                c.execute("INSERT INTO member_notes (member, note) VALUES (?,?)", (m, txt or ""))
        c.commit()


def todo_add_to_db(tid, title, done, audience="all", sort_order=0, detail=""):
    with _conn() as c:
        so = sort_order if sort_order is not None else 0
        dtl = (detail or "").strip()
        try:
            c.execute("INSERT INTO todos (id, title, done, audience, sort_order, detail) VALUES (?,?,?,?,?,?)", (tid, title, 1 if done else 0, audience or "all", so, dtl))
        except sqlite3.OperationalError:
            try:
                c.execute("INSERT INTO todos (id, title, done, audience, sort_order) VALUES (?,?,?,?,?)", (tid, title, 1 if done else 0, audience or "all", so))
            except sqlite3.OperationalError:
                try:
                    c.execute("INSERT INTO todos (id, title, done, audience) VALUES (?,?,?,?)", (tid, title, 1 if done else 0, audience or "all"))
                except sqlite3.OperationalError:
                    c.execute("INSERT INTO todos (id, title, done) VALUES (?,?,?,?)", (tid, title, 1 if done else 0))
        c.commit()


def todo_toggle_in_db(tid, done):
    with _conn() as c:
        c.execute("UPDATE todos SET done = ? WHERE id = ?", (1 if done else 0, tid))
        c.commit()


def todo_delete_from_db(tid):
    with _conn() as c:
        c.execute("DELETE FROM todos WHERE id = ?", (tid,))
        c.commit()


def todo_update_in_db(tid, title, audience="all", sort_order=None, detail=None):
    with _conn() as c:
        try:
            if detail is not None:
                try:
                    if sort_order is not None:
                        c.execute("UPDATE todos SET title = ?, audience = ?, sort_order = ?, detail = ? WHERE id = ?", (title or "", audience or "all", sort_order, (detail or "").strip(), tid))
                    else:
                        c.execute("UPDATE todos SET title = ?, audience = ?, detail = ? WHERE id = ?", (title or "", audience or "all", (detail or "").strip(), tid))
                except sqlite3.OperationalError:
                    try:
                        c.execute("ALTER TABLE todos ADD COLUMN detail TEXT DEFAULT ''")
                        c.commit()
                    except sqlite3.OperationalError:
                        pass
                    try:
                        if sort_order is not None:
                            c.execute("UPDATE todos SET title = ?, audience = ?, sort_order = ?, detail = ? WHERE id = ?", (title or "", audience or "all", sort_order, (detail or "").strip(), tid))
                        else:
                            c.execute("UPDATE todos SET title = ?, audience = ?, detail = ? WHERE id = ?", (title or "", audience or "all", (detail or "").strip(), tid))
                    except sqlite3.OperationalError:
                        if sort_order is not None:
                            c.execute("UPDATE todos SET title = ?, audience = ?, sort_order = ? WHERE id = ?", (title or "", audience or "all", sort_order, tid))
                        else:
                            c.execute("UPDATE todos SET title = ?, audience = ? WHERE id = ?", (title or "", audience or "all", tid))
            elif sort_order is not None:
                c.execute("UPDATE todos SET title = ?, audience = ?, sort_order = ? WHERE id = ?", (title or "", audience or "all", sort_order, tid))
            else:
                c.execute("UPDATE todos SET title = ?, audience = ? WHERE id = ?", (title or "", audience or "all", tid))
        except sqlite3.OperationalError:
            try:
                c.execute("UPDATE todos SET title = ?, audience = ? WHERE id = ?", (title or "", audience or "all", tid))
            except sqlite3.OperationalError:
                c.execute("UPDATE todos SET title = ? WHERE id = ?", (title or "", tid))
        c.commit()


def _seed_admin():
    try:
        with _conn() as c:
            n = c.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
            if n == 0:
                c.execute("INSERT INTO admin_users (id, password_hash) VALUES (?,?)", ("admin", "12345"))
                c.commit()
    except sqlite3.OperationalError:
        pass


def load_admin_password_hash():
    try:
        with _conn() as c:
            r = c.execute("SELECT password_hash FROM admin_users WHERE id = ?", ("admin",)).fetchone()
            return r[0] if r else None
    except sqlite3.OperationalError:
        return None


def admin_update_password(new_password: str):
    with _conn() as c:
        c.execute("UPDATE admin_users SET password_hash = ? WHERE id = ?", (new_password, "admin"))
        c.commit()


def record_login(user_id: str, ip: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO user_last_login (user_id, last_login_at, last_login_ip) VALUES (?,?,?)",
                (user_id, now, ip),
            )
            c.commit()
    except sqlite3.OperationalError:
        pass


def load_last_logins():
    out = {}
    try:
        with _conn() as c:
            for row in c.execute("SELECT user_id, last_login_at, last_login_ip FROM user_last_login"):
                out[row[0]] = {"at": row[1], "ip": row[2] or ""}
    except sqlite3.OperationalError:
        pass
    return out




def _seed_members_partners():
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) FROM members").fetchone()[0]
        if n == 0:
            for i, (m, pw) in enumerate([("integlab", "12345"), ("choiworks", "12345"), ("momentcube", "12345"), ("doddle", "12345")]):
                try:
                    c.execute("INSERT INTO members (id, password, sort_order) VALUES (?,?,?)", (m, pw, i))
                except sqlite3.OperationalError:
                    c.execute("INSERT INTO members (id, password) VALUES (?,?)", (m, pw))
        n = c.execute("SELECT COUNT(*) FROM partners").fetchone()[0]
        if n == 0:
            try:
                c.execute("INSERT INTO partners (id, password, sort_order) VALUES (?,?,?)", ("whimory", "12345", 0))
            except sqlite3.OperationalError:
                c.execute("INSERT INTO partners (id, password) VALUES (?,?)", ("whimory", "12345"))
        c.commit()


def load_members_from_db():
    with _conn() as c:
        try:
            rows = c.execute("SELECT id, password, sort_order, equity FROM members ORDER BY sort_order, id")
        except sqlite3.OperationalError:
            try:
                rows = c.execute("SELECT id, password, sort_order FROM members ORDER BY sort_order, id")
            except sqlite3.OperationalError:
                rows = c.execute("SELECT id, password FROM members ORDER BY id")
        out = []
        for r in rows:
            row = list(r)
            if len(row) == 2:
                row.extend([0, ""])
            elif len(row) == 3:
                row.append("")
            out.append({
                "id": row[0], "password": row[1],
                "sort_order": row[2] if row[2] is not None else 0,
                "equity": (row[3] or "").strip() if len(row) > 3 else ""
            })
        return out


def load_partners_from_db():
    with _conn() as c:
        try:
            rows = c.execute("SELECT id, password, sort_order, equity FROM partners ORDER BY sort_order, id")
        except sqlite3.OperationalError:
            try:
                rows = c.execute("SELECT id, password, sort_order FROM partners ORDER BY sort_order, id")
            except sqlite3.OperationalError:
                rows = c.execute("SELECT id, password FROM partners ORDER BY id")
        out = []
        for r in rows:
            row = list(r)
            if len(row) == 2:
                row.extend([0, ""])
            elif len(row) == 3:
                row.append("")
            out.append({
                "id": row[0], "password": row[1],
                "sort_order": row[2] if row[2] is not None else 0,
                "equity": (row[3] or "").strip() if len(row) > 3 else ""
            })
        return out


def member_add_to_db(mid, password, sort_order=None, equity=""):
    with _conn() as c:
        if sort_order is None:
            try:
                rows = list(c.execute("SELECT COALESCE(MAX(sort_order), -1) FROM members"))
                sort_order = (rows[0][0] + 1) if rows else 0
            except sqlite3.OperationalError:
                sort_order = 0
        pw_hash = _hash_password(password) if ":" not in password else password
        eq = (equity or "").strip()
        try:
            c.execute("INSERT INTO members (id, password, sort_order, equity) VALUES (?,?,?,?)", (mid.strip(), pw_hash, sort_order, eq))
        except sqlite3.OperationalError:
            try:
                c.execute("INSERT INTO members (id, password, sort_order) VALUES (?,?,?)", (mid.strip(), pw_hash, sort_order))
            except sqlite3.OperationalError:
                c.execute("INSERT INTO members (id, password) VALUES (?,?)", (mid.strip(), pw_hash))
        c.commit()


def member_update_in_db(mid, password=None, sort_order=None, equity=None):
    with _conn() as c:
        if password is not None:
            c.execute("UPDATE members SET password = ? WHERE id = ?", (password, mid))
        if sort_order is not None:
            try:
                c.execute("UPDATE members SET sort_order = ? WHERE id = ?", (sort_order, mid))
            except sqlite3.OperationalError:
                pass
        if equity is not None:
            try:
                c.execute("UPDATE members SET equity = ? WHERE id = ?", ((equity or "").strip(), mid))
            except sqlite3.OperationalError:
                pass
        c.commit()


def member_delete_from_db(mid):
    with _conn() as c:
        try:
            c.execute("DELETE FROM member_badges WHERE member_id = ?", (mid,))
        except sqlite3.OperationalError:
            pass
        c.execute("DELETE FROM members WHERE id = ?", (mid,))
        c.commit()


def load_badges_by_member():
    out = {}
    try:
        with _conn() as c:
            for row in c.execute("SELECT id, member_id, mission_name, icon_type FROM member_badges ORDER BY id"):
                bid, mid, mname, itype = row
                if mid not in out:
                    out[mid] = []
                out[mid].append({"id": bid, "mission_name": mname, "icon_type": itype})
    except sqlite3.OperationalError:
        pass
    return out


def badge_add_to_db(member_id, mission_name, icon_type):
    with _conn() as c:
        c.execute(
            "INSERT INTO member_badges (member_id, mission_name, icon_type) VALUES (?,?,?)",
            (member_id, mission_name, icon_type),
        )
        c.commit()


def badge_update_in_db(badge_id, mission_name, icon_type):
    with _conn() as c:
        c.execute("UPDATE member_badges SET mission_name = ?, icon_type = ? WHERE id = ?", (mission_name, icon_type, badge_id))
        c.commit()


def badge_delete_from_db(badge_id):
    with _conn() as c:
        c.execute("DELETE FROM member_badges WHERE id = ?", (badge_id,))
        c.commit()


def partner_add_to_db(pid, password, sort_order=None, equity=""):
    with _conn() as c:
        if sort_order is None:
            try:
                rows = list(c.execute("SELECT COALESCE(MAX(sort_order), -1) FROM partners"))
                sort_order = (rows[0][0] + 1) if rows else 0
            except sqlite3.OperationalError:
                sort_order = 0
        pw_hash = _hash_password(password) if ":" not in password else password
        eq = (equity or "").strip()
        try:
            c.execute("INSERT INTO partners (id, password, sort_order, equity) VALUES (?,?,?,?)", (pid.strip(), pw_hash, sort_order, eq))
        except sqlite3.OperationalError:
            try:
                c.execute("INSERT INTO partners (id, password, sort_order) VALUES (?,?,?)", (pid.strip(), pw_hash, sort_order))
            except sqlite3.OperationalError:
                c.execute("INSERT INTO partners (id, password) VALUES (?,?)", (pid.strip(), pw_hash))
        c.commit()


def partner_update_in_db(pid, password=None, sort_order=None, equity=None):
    with _conn() as c:
        if password is not None:
            c.execute("UPDATE partners SET password = ? WHERE id = ?", (password, pid))
        if sort_order is not None:
            try:
                c.execute("UPDATE partners SET sort_order = ? WHERE id = ?", (sort_order, pid))
            except sqlite3.OperationalError:
                pass
        if equity is not None:
            try:
                c.execute("UPDATE partners SET equity = ? WHERE id = ?", ((equity or "").strip(), pid))
            except sqlite3.OperationalError:
                pass
        c.commit()


def partner_delete_from_db(pid):
    with _conn() as c:
        c.execute("DELETE FROM partners WHERE id = ?", (pid,))
        c.commit()


def load_backers_from_db():
    with _conn() as c:
        try:
            rows = c.execute("SELECT id, password, sort_order, equity FROM backers ORDER BY sort_order, id")
        except sqlite3.OperationalError:
            try:
                rows = c.execute("SELECT id, password, sort_order FROM backers ORDER BY sort_order, id")
            except sqlite3.OperationalError:
                rows = c.execute("SELECT id, password FROM backers ORDER BY id")
        out = []
        for r in rows:
            row = list(r)
            if len(row) == 2:
                row.extend([0, ""])
            elif len(row) == 3:
                row.append("")
            out.append({
                "id": row[0], "password": row[1],
                "sort_order": row[2] if row[2] is not None else 0,
                "equity": (row[3] or "").strip() if len(row) > 3 else ""
            })
        return out


def load_customers_from_db():
    with _conn() as c:
        try:
            rows = c.execute("SELECT id, password, sort_order, equity FROM customers ORDER BY sort_order, id")
        except sqlite3.OperationalError:
            try:
                rows = c.execute("SELECT id, password, sort_order FROM customers ORDER BY sort_order, id")
            except sqlite3.OperationalError:
                rows = c.execute("SELECT id, password FROM customers ORDER BY id")
        out = []
        for r in rows:
            row = list(r)
            if len(row) == 2:
                row.extend([0, ""])
            elif len(row) == 3:
                row.append("")
            out.append({
                "id": row[0], "password": row[1],
                "sort_order": row[2] if row[2] is not None else 0,
                "equity": (row[3] or "").strip() if len(row) > 3 else ""
            })
        return out


def backer_add_to_db(bid, password, sort_order=None, equity=""):
    with _conn() as c:
        if sort_order is None:
            try:
                rows = list(c.execute("SELECT COALESCE(MAX(sort_order), -1) FROM backers"))
                sort_order = (rows[0][0] + 1) if rows else 0
            except sqlite3.OperationalError:
                sort_order = 0
        pw_plain = (password or "").strip()
        eq = (equity or "").strip()
        try:
            c.execute("INSERT INTO backers (id, password, sort_order, equity) VALUES (?,?,?,?)", (bid.strip(), pw_plain, sort_order, eq))
        except sqlite3.OperationalError:
            try:
                c.execute("INSERT INTO backers (id, password, sort_order) VALUES (?,?,?)", (bid.strip(), pw_plain, sort_order))
            except sqlite3.OperationalError:
                c.execute("INSERT INTO backers (id, password) VALUES (?,?)", (bid.strip(), pw_plain))
        c.commit()


def backer_update_in_db(bid, password=None, sort_order=None, equity=None):
    with _conn() as c:
        if password is not None:
            c.execute("UPDATE backers SET password = ? WHERE id = ?", (password, bid))
        if sort_order is not None:
            try:
                c.execute("UPDATE backers SET sort_order = ? WHERE id = ?", (sort_order, bid))
            except sqlite3.OperationalError:
                pass
        if equity is not None:
            try:
                c.execute("UPDATE backers SET equity = ? WHERE id = ?", ((equity or "").strip(), bid))
            except sqlite3.OperationalError:
                pass
        c.commit()


def backer_delete_from_db(bid):
    with _conn() as c:
        c.execute("DELETE FROM backers WHERE id = ?", (bid,))
        c.commit()


def customer_add_to_db(cid, password, sort_order=None, equity=""):
    with _conn() as c:
        if sort_order is None:
            try:
                rows = list(c.execute("SELECT COALESCE(MAX(sort_order), -1) FROM customers"))
                sort_order = (rows[0][0] + 1) if rows else 0
            except sqlite3.OperationalError:
                sort_order = 0
        pw_plain = (password or "").strip()
        eq = (equity or "").strip()
        try:
            c.execute("INSERT INTO customers (id, password, sort_order, equity) VALUES (?,?,?,?)", (cid.strip(), pw_plain, sort_order, eq))
        except sqlite3.OperationalError:
            try:
                c.execute("INSERT INTO customers (id, password, sort_order) VALUES (?,?,?)", (cid.strip(), pw_plain, sort_order))
            except sqlite3.OperationalError:
                c.execute("INSERT INTO customers (id, password) VALUES (?,?)", (cid.strip(), pw_plain))
        c.commit()


def customer_update_in_db(cid, password=None, sort_order=None, equity=None):
    with _conn() as c:
        if password is not None:
            c.execute("UPDATE customers SET password = ? WHERE id = ?", (password, cid))
        if sort_order is not None:
            try:
                c.execute("UPDATE customers SET sort_order = ? WHERE id = ?", (sort_order, cid))
            except sqlite3.OperationalError:
                pass
        if equity is not None:
            try:
                c.execute("UPDATE customers SET equity = ? WHERE id = ?", ((equity or "").strip(), cid))
            except sqlite3.OperationalError:
                pass
        c.commit()


def customer_delete_from_db(cid):
    with _conn() as c:
        c.execute("DELETE FROM customers WHERE id = ?", (cid,))
        c.commit()


def _login_member(mid, password):
    for m in load_members_from_db():
        if m["id"] == mid and _verify_password(password, m["password"]):
            return True
    return False


def _login_partner(pid, password):
    for p in load_partners_from_db():
        if p["id"] == pid and _verify_password(password, p["password"]):
            return True
    return False


def _login_backer(bid, password):
    for b in load_backers_from_db():
        if b["id"] == bid and (password or "") == (b.get("password") or ""):
            return True
    return False


def _login_customer(cid, password):
    for c in load_customers_from_db():
        if c["id"] == cid and (password or "") == (c.get("password") or ""):
            return True
    return False


def _login_admin(password):
    h = load_admin_password_hash()
    return bool(h and (password == h or _verify_password(password, h)))

YEARS = ["2025", "2026", "2027", "2028", "2029", "2030"]
_YEARS_TUPLE = tuple(YEARS)

# 사용자별 연도별 납부 데이터 (DB members/partners/backers/customers 기준)
MEMBER_DATA = {}
PARTNER_DATA = {}
BACKER_DATA = {}
CUSTOMER_DATA = {}
MEMBER_NOTES = {}
MEMBER_NOTE_DATES = {}  # {member: "2026-01-30 14:30:00"}

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

def find_todo(todo_id):
    for todo in load_todos_from_db():
        if todo['id'] == todo_id:
            return todo
    return None


def _migrate_hash_to_plain():
    """해시된 비밀번호를 12345로 초기화 (표시를 위해 평문 저장)"""
    try:
        with _conn() as c:
            for m in load_members_from_db():
                pw = m.get("password") or ""
                if pw and ":" in pw:
                    c.execute("UPDATE members SET password = ? WHERE id = ?", ("12345", m["id"]))
            for p in load_partners_from_db():
                pw = p.get("password") or ""
                if pw and ":" in pw:
                    c.execute("UPDATE partners SET password = ? WHERE id = ?", ("12345", p["id"]))
            try:
                r = c.execute("SELECT password_hash FROM admin_users WHERE id = ?", ("admin",)).fetchone()
                if r and r[0] and ":" in str(r[0]):
                    c.execute("UPDATE admin_users SET password_hash = ? WHERE id = ?", ("12345", "admin"))
            except sqlite3.OperationalError:
                pass
            c.commit()
    except Exception:
        pass


@app.on_event("startup")
def on_startup():
    init_db()
    _migrate_hash_to_plain()
    member_ids = {m["id"] for m in load_members_from_db()}
    partner_ids = {p["id"] for p in load_partners_from_db()}
    backer_ids = {b["id"] for b in load_backers_from_db()}
    customer_ids = {c["id"] for c in load_customers_from_db()}
    for mid in member_ids:
        MEMBER_DATA[mid] = {y: [False] * 12 for y in _YEARS_TUPLE}
        MEMBER_NOTES[mid] = ""
    for pid in partner_ids:
        PARTNER_DATA[pid] = {y: [False] * 12 for y in _YEARS_TUPLE}
    for bid in backer_ids:
        BACKER_DATA[bid] = {y: [False] * 12 for y in _YEARS_TUPLE}
    for cid in customer_ids:
        CUSTOMER_DATA[cid] = {y: [False] * 12 for y in _YEARS_TUPLE}
    loaded = load_contrib_from_db()
    for uid, ys in loaded.items():
        target = None
        if uid in MEMBER_DATA:
            target = MEMBER_DATA
        elif uid in PARTNER_DATA:
            target = PARTNER_DATA
        elif uid in BACKER_DATA:
            target = BACKER_DATA
        elif uid in CUSTOMER_DATA:
            target = CUSTOMER_DATA
        if target:
            for y, months in ys.items():
                if y in target[uid] and len(months) == 12:
                    target[uid][y] = months[:]
    notes_dict, note_dates = load_notes_from_db()
    for m, txt in notes_dict.items():
        if m in MEMBER_NOTES:
            MEMBER_NOTES[m] = txt
            if note_dates.get(m):
                MEMBER_NOTE_DATES[m] = note_dates[m]


CONTACT_EMAIL = "remylee@naver.com"

# Contact 폼 rate limiting (IP당 10분에 최대 3회)
_CONTACT_RATE_LIMIT: dict = defaultdict(list)
_CONTACT_RATE_WINDOW = 600  # 10분(초)
_CONTACT_RATE_MAX = 3


def _check_contact_rate_limit(client_ip: str) -> Optional[str]:
    """제한 초과 시 에러 메시지 반환, 아니면 None."""
    now = time.time()
    lst = _CONTACT_RATE_LIMIT[client_ip]
    lst[:] = [t for t in lst if now - t < _CONTACT_RATE_WINDOW]
    if len(lst) >= _CONTACT_RATE_MAX:
        return f"잠시 후 다시 시도해 주세요. (10분당 최대 {_CONTACT_RATE_MAX}회)"
    lst.append(now)
    return None


def _get_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _send_contact_email(
    company: str, name: str, email: str, phone: str, message: str,
    location: str = "", revenue: str = "", employees: str = "", industry: str = "",
    years: str = "", interest: str = "", company_url: str = ""
) -> Optional[str]:
    """문의 메일을 remylee@naver.com으로 전송 + 발신자에게 자동 답장. 실패 시 에러 메시지 반환."""
    smtp_user = (os.environ.get("SMTP_USER", CONTACT_EMAIL) or "").strip()
    smtp_password = (os.environ.get("SMTP_PASSWORD", "") or "").strip()
    if not smtp_password:
        return "SMTP_PASSWORD 환경변수를 설정해주세요. (네이버 메일 설정에서 앱 비밀번호 생성)"
    smtp_host = os.environ.get("SMTP_HOST", "smtp.naver.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    extra = []
    if location:
        extra.append(f"회사 소재지: {location}")
    if revenue:
        extra.append(f"연매출: {revenue}")
    if employees:
        extra.append(f"임직원수: {employees}")
    if industry:
        extra.append(f"업종: {industry}")
    if years:
        extra.append(f"업력: {years}")
    if interest:
        extra.append(f"관심분야: {interest}")
    if company_url:
        extra.append(f"회사 홈페이지 주소: {company_url}")
    extra_str = "\n".join(extra) if extra else "(미선택)"
    body_admin = f"""YOTTA LAB 웹사이트 문의가 접수되었습니다.

기업명: {company}
담당자: {name}
이메일: {email}
연락처: {phone}

문의 내용:
{message}

[추가 정보] (선택 항목)
{extra_str}

---
본 메일은 YOTTA LAB 홈페이지 Contact 폼에서 자동 전송되었습니다.
"""
    body_reply = f"""안녕하세요, {name}님.

YOTTA LAB 홈페이지를 이용해 주셔서 감사합니다.

문의하신 내용을 잘 받았습니다.
확인 후 빠른 시일 내에 연락드리겠습니다.

감사합니다.
YOTTA LAB
"""
    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port) as s:
                s.login(smtp_user, smtp_password)
                msg_admin = MIMEMultipart()
                msg_admin["Subject"] = f"[YOTTA LAB 문의] {company} - {name}"
                msg_admin["From"] = smtp_user
                msg_admin["To"] = CONTACT_EMAIL
                msg_admin.attach(MIMEText(body_admin, "plain", "utf-8"))
                s.sendmail(smtp_user, CONTACT_EMAIL, msg_admin.as_string())
                msg_reply = MIMEMultipart()
                msg_reply["Subject"] = "[YOTTA LAB] 문의 접수 확인"
                msg_reply["From"] = smtp_user
                msg_reply["To"] = email
                msg_reply.attach(MIMEText(body_reply, "plain", "utf-8"))
                s.sendmail(smtp_user, email, msg_reply.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port) as s:
                s.starttls()
                s.login(smtp_user, smtp_password)
                msg_admin = MIMEMultipart()
                msg_admin["Subject"] = f"[YOTTA LAB 문의] {company} - {name}"
                msg_admin["From"] = smtp_user
                msg_admin["To"] = CONTACT_EMAIL
                msg_admin.attach(MIMEText(body_admin, "plain", "utf-8"))
                s.sendmail(smtp_user, CONTACT_EMAIL, msg_admin.as_string())
                msg_reply = MIMEMultipart()
                msg_reply["Subject"] = "[YOTTA LAB] 문의 접수 확인"
                msg_reply["From"] = smtp_user
                msg_reply["To"] = email
                msg_reply.attach(MIMEText(body_reply, "plain", "utf-8"))
                s.sendmail(smtp_user, email, msg_reply.as_string())
    except Exception as e:
        err_msg = str(e)
        print(f"[SMTP 오류] {err_msg}")
        return err_msg
    return None


@app.get("/demo-concierge")
async def demo_concierge(request: Request):
    """정부지원사업 컨시어지 서비스"""
    current_user = request.cookies.get("current_user")
    return templates.TemplateResponse("demo_concierge.html", {
        "request": request,
        "username": current_user,
    })


@app.get("/api/bizinfo")
async def api_bizinfo():
    """비즈인포 사업공고 API 호출 결과를 JSON으로 반환"""
    try:
        from fetch_bizinfo_api import fetch_bizinfo_announcements
        result = fetch_bizinfo_announcements(search_cnt=20, data_type="json")
        return result
    except ValueError as e:
        return {"error": str(e), "hint": "환경변수 BIZINFO_API_KEY 또는 .env에 API 키를 설정하세요."}
    except Exception as e:
        return {"error": str(e)}


@app.get("/")
async def home(request: Request, sent: str = None, error: str = None, error_msg: str = None):
    current_user = request.cookies.get("current_user")
    return templates.TemplateResponse("index.html", {
        "request": request,
        "username": current_user,
        "contact_sent": sent == "1",
        "contact_error": error == "1",
        "contact_error_msg": error_msg or "",
    })


@app.post("/contact")
async def contact_submit(
    request: Request,
    company: str = Form(...),
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    message: str = Form(...),
    website: str = Form(""),  # honeypot: 봇이 채우면 차단
    location: str = Form(""),
    revenue: str = Form(""),
    employees: str = Form(""),
    industry: str = Form(""),
    years: str = Form(""),
    interest: str = Form(""),
    company_url: str = Form(""),
):
    if website:
        return RedirectResponse(url="/?sent=1#contact", status_code=303)  # 봇은 성공처럼 보이게
    client_ip = _get_client_ip(request)
    rate_err = _check_contact_rate_limit(client_ip)
    if rate_err:
        return RedirectResponse(url=f"/?error=1&error_msg={quote(rate_err)}#contact", status_code=303)
    err = _send_contact_email(
        company, name, email, phone, message,
        location=(location or "").strip(), revenue=(revenue or "").strip(),
        employees=(employees or "").strip(), industry=(industry or "").strip(),
        years=(years or "").strip(), interest=(interest or "").strip(),
        company_url=(company_url or "").strip(),
    )
    if err:
        return RedirectResponse(url=f"/?error=1&error_msg={quote(err)}#contact", status_code=303)
    return RedirectResponse(url="/?sent=1#contact", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie("current_user", path="/")
    return response

@app.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    ok = False
    if username == "admin":
        ok = _login_admin(password)
    elif _login_member(username, password) or _login_partner(username, password) or _login_backer(username, password) or _login_customer(username, password):
        ok = True
    if not ok:
        return RedirectResponse(url="/login?error=invalid", status_code=303)
    record_login(username, _get_client_ip(request))
    res = RedirectResponse(url="/dashboard?year=2026", status_code=303)
    # max_age 미설정 = 세션 쿠키 → 브라우저 종료 시 자동 로그아웃
    res.set_cookie(key="current_user", value=username, httponly=True, samesite="lax", path="/")
    return res

@app.get("/dashboard")
async def dashboard(request: Request, year: str = "2026", tab: str = None, contrib: str = None, pwd_ok: str = None, pwd_error: str = None):
    current_user = request.cookies.get("current_user")
    if not current_user:
        return RedirectResponse(url="/login")
    initial_tab = tab if tab in ("todo", "contribution", "status") else "todo"
    is_admin = (current_user == "admin")
    member_badges = load_badges_by_member()
    last_logins = load_last_logins()
    members_with_badges = []
    for m in load_members_from_db():
        m2 = {"id": m["id"], "sort_order": m["sort_order"], "badges": member_badges.get(m["id"], [])}
        members_with_badges.append(m2)
    members_with_login = []
    for m in load_members_from_db():
        ll = last_logins.get(m["id"], {})
        m2 = dict(m)
        m2["last_login_at"] = ll.get("at", "")
        m2["last_login_ip"] = ll.get("ip", "")
        members_with_login.append(m2)
    partners_with_login = []
    for p in load_partners_from_db():
        ll = last_logins.get(p["id"], {})
        p2 = dict(p)
        p2["last_login_at"] = ll.get("at", "")
        p2["last_login_ip"] = ll.get("ip", "")
        partners_with_login.append(p2)
    backers_with_login = []
    for b in load_backers_from_db():
        ll = last_logins.get(b["id"], {})
        b2 = dict(b)
        b2["last_login_at"] = ll.get("at", "")
        b2["last_login_ip"] = ll.get("ip", "")
        backers_with_login.append(b2)
    customers_with_login = []
    for c in load_customers_from_db():
        ll = last_logins.get(c["id"], {})
        c2 = dict(c)
        c2["last_login_at"] = ll.get("at", "")
        c2["last_login_ip"] = ll.get("ip", "")
        customers_with_login.append(c2)
    if is_admin:
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "username": current_user,
            "is_admin": True,
            "member_data": MEMBER_DATA,
            "partner_data": PARTNER_DATA,
            "backer_data": BACKER_DATA,
            "customer_data": CUSTOMER_DATA,
            "contrib_subtab": contrib if contrib in ("member", "partner", "backer", "customer") else "member",
            "member_notes": MEMBER_NOTES,
            "member_note_dates": MEMBER_NOTE_DATES,
            "years": YEARS,
            "year": year,
            "months": MONTHS,
            "todos": get_todos_for_user(current_user),
            "initial_tab": initial_tab,
            "members": members_with_login,
            "members_with_badges": members_with_badges,
            "partners": partners_with_login,
            "backers": backers_with_login,
            "customers": customers_with_login,
            "admin_password": load_admin_password_hash() or "",
            "members_json": json.dumps(members_with_badges),
            "partners_json": json.dumps(load_partners_from_db()),
            "backers_json": json.dumps(load_backers_from_db()),
            "customers_json": json.dumps(load_customers_from_db()),
            "badge_icons": BADGE_ICONS,
            "badge_icons_json": json.dumps(BADGE_ICONS),
            "pwd_ok": pwd_ok,
            "pwd_error": pwd_error,
        })
    # 일반 유저 기존 화면
    member_ids = {m["id"] for m in load_members_from_db()}
    partner_ids = {p["id"] for p in load_partners_from_db()}
    backer_ids = {b["id"] for b in load_backers_from_db()}
    customer_ids = {c["id"] for c in load_customers_from_db()}
    if current_user in member_ids:
        user_role = "member"
    elif current_user in partner_ids:
        user_role = "partner"
    elif current_user in backer_ids:
        user_role = "backer"
    elif current_user in customer_ids:
        user_role = "customer"
    else:
        user_role = "member"  # fallback
    is_partner = (current_user not in MEMBER_DATA)
    if current_user in MEMBER_DATA:
        user_yearly_data = MEMBER_DATA.get(current_user, {})
    elif current_user in PARTNER_DATA:
        user_yearly_data = PARTNER_DATA.get(current_user, {})
    elif current_user in BACKER_DATA:
        user_yearly_data = BACKER_DATA.get(current_user, {})
    elif current_user in CUSTOMER_DATA:
        user_yearly_data = CUSTOMER_DATA.get(current_user, {})
    else:
        user_yearly_data = {}
    data = user_yearly_data.get(year, [False] * 12)
    payment_count = data.count(True)
    status_list = [{"month": m, "paid": p} for m, p in zip(MONTHS, data)]
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "username": current_user,
        "is_admin": False,
        "is_partner": is_partner,
        "year": year,
        "years": YEARS,
        "status_list": status_list,
        "total_count": payment_count,
        "todos": get_todos_for_user(current_user),
        "initial_tab": initial_tab,
        "user_role": user_role,
        "member_note": MEMBER_NOTES.get(current_user, ""),
        "member_note_date": _format_note_date(MEMBER_NOTE_DATES.get(current_user, "")),
        "members": load_members_from_db(),
        "partners": load_partners_from_db(),
        "backers": load_backers_from_db(),
        "customers": load_customers_from_db(),
        "members_json": json.dumps(members_with_badges),
        "partners_json": json.dumps(load_partners_from_db()),
        "backers_json": json.dumps(load_backers_from_db()),
        "customers_json": json.dumps(load_customers_from_db()),
        "badge_icons": BADGE_ICONS,
        "badge_icons_json": json.dumps(BADGE_ICONS),
    })

# Admin - Contribution(납부현황) 관리 (대시보드 내 폼에서 POST)
@app.post("/dashboard/admin/edit-contrib")
async def admin_edit_contrib(request: Request):
    admin = request.cookies.get("current_user")
    if admin != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    form = await request.form()
    contrib_type = form.get("contrib_type") or "member"
    global MEMBER_DATA, MEMBER_NOTES, MEMBER_NOTE_DATES, PARTNER_DATA, BACKER_DATA, CUSTOMER_DATA
    # Update the appropriate data dict based on contrib_type
    if contrib_type == "member":
        new_member = {m: {y: [False]*12 for y in sd.keys()} for m, sd in MEMBER_DATA.items()}
        for key in form:
            if key.startswith("data_"):
                try:
                    _, uid, year, month = key.split("_")
                    month = int(month)
                    if uid in new_member and year in new_member[uid]:
                        new_member[uid][year][month] = True
                except Exception:
                    pass
            elif key.startswith("note_"):
                uid = key[5:]
                if uid and uid in MEMBER_NOTES:
                    MEMBER_NOTES[uid] = (form.get(key) or "").strip()
        MEMBER_DATA = new_member
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for m in MEMBER_NOTES:
            MEMBER_NOTE_DATES[m] = now
        save_notes_to_db(MEMBER_NOTES)
    elif contrib_type == "partner":
        new_partner = {p: {y: [False]*12 for y in sd.keys()} for p, sd in PARTNER_DATA.items()}
        for key in form:
            if key.startswith("data_"):
                try:
                    _, uid, year, month = key.split("_")
                    month = int(month)
                    if uid in new_partner and year in new_partner[uid]:
                        new_partner[uid][year][month] = True
                except Exception:
                    pass
        PARTNER_DATA = new_partner
    elif contrib_type == "backer":
        new_backer = {b: {y: [False]*12 for y in sd.keys()} for b, sd in BACKER_DATA.items()}
        for key in form:
            if key.startswith("data_"):
                try:
                    _, uid, year, month = key.split("_")
                    month = int(month)
                    if uid in new_backer and year in new_backer[uid]:
                        new_backer[uid][year][month] = True
                except Exception:
                    pass
        BACKER_DATA = new_backer
    elif contrib_type == "customer":
        new_customer = {c: {y: [False]*12 for y in sd.keys()} for c, sd in CUSTOMER_DATA.items()}
        for key in form:
            if key.startswith("data_"):
                try:
                    _, uid, year, month = key.split("_")
                    month = int(month)
                    if uid in new_customer and year in new_customer[uid]:
                        new_customer[uid][year][month] = True
                except Exception:
                    pass
        CUSTOMER_DATA = new_customer
    save_contrib_to_db(_merge_all_contrib())
    redirect_tab = form.get("contrib_subtab") or "member"
    return RedirectResponse(f"/dashboard?tab=contribution&contrib={redirect_tab}", status_code=303)

# Admin - Todo 추가
@app.post("/dashboard/admin/todo_add")
async def admin_todo_add(request: Request):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    form = await request.form()
    todo_title = (form.get("todo_title") or "").strip()
    if not todo_title:
        return RedirectResponse("/dashboard?tab=todo", status_code=303)
    all_todos = load_todos_from_db()
    next_id = (max([t["id"] for t in all_todos], default=0) + 1) if all_todos else 1
    sort_order = 0
    so_val = form.get("todo_sort_order")
    if so_val is not None and so_val != "":
        try:
            sort_order = int(so_val)
        except ValueError:
            sort_order = max([t.get("sort_order", 0) for t in all_todos], default=-1) + 1
    else:
        sort_order = max([t.get("sort_order", 0) for t in all_todos], default=-1) + 1
    at = form.get("audience_type") or "all"
    if at == "selected":
        ids = []
        for k in form:
            if k.startswith("audience_") and k != "audience_type" and form.get(k):
                ids.append(k.replace("audience_", ""))
        audience = ",".join(ids) if ids else "all"
    else:
        audience = at  # all, members, partners
    todo_detail = (form.get("todo_detail") or "").strip()
    todo_add_to_db(next_id, todo_title, False, audience, sort_order, todo_detail)
    return RedirectResponse("/dashboard?tab=todo", status_code=303)

# Admin - Todo 체크/미체크(완료상태)
@app.post("/dashboard/admin/todo_toggle/{todo_id}")
async def admin_todo_toggle(request: Request, todo_id: int):
    admin = request.cookies.get("current_user")
    if admin != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    todo = find_todo(todo_id)
    if todo:
        todo['done'] = not todo['done']
        todo_toggle_in_db(todo_id, todo['done'])
    return RedirectResponse("/dashboard?tab=todo", status_code=303)

# Admin - Todo 삭제
@app.post("/dashboard/admin/todo_edit")
async def admin_todo_edit(request: Request):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    form = await request.form()
    todo_id = form.get("todo_id")
    todo_title = (form.get("todo_title") or "").strip()
    if not todo_id:
        return RedirectResponse("/dashboard?tab=todo", status_code=303)
    try:
        tid = int(todo_id)
    except ValueError:
        return RedirectResponse("/dashboard?tab=todo", status_code=303)
    # 상세 내용만 수정하는 경우 제목이 비어 올 수 있음 - 기존 제목 사용
    if not todo_title:
        existing = find_todo(tid)
        todo_title = (existing.get("title") or "").strip() if existing else ""
    at = form.get("audience_type") or "all"
    if at == "selected":
        ids = []
        for k in form:
            if k.startswith("audience_") and k != "audience_type" and form.get(k):
                ids.append(k.replace("audience_", ""))
        audience = ",".join(ids) if ids else "all"
    else:
        audience = at  # all, members, partners
    sort_order = None
    so_val = form.get("todo_sort_order")
    if so_val is not None and so_val != "":
        try:
            sort_order = int(so_val)
        except ValueError:
            pass
    todo_detail = (form.get("todo_detail") or "").strip()
    todo_update_in_db(tid, todo_title, audience, sort_order, todo_detail)
    return RedirectResponse("/dashboard?tab=todo", status_code=303)


@app.post("/dashboard/admin/todo_delete/{todo_id}")
async def admin_todo_delete(request: Request, todo_id: int):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    todo_delete_from_db(todo_id)
    return RedirectResponse("/dashboard?tab=todo", status_code=303)

@app.post("/dashboard/admin/password/change")
async def admin_password_change(request: Request):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    form = await request.form()
    current = (form.get("current_password") or "").strip()
    new_pw = (form.get("new_password") or "").strip()
    new_confirm = (form.get("new_password_confirm") or "").strip()
    if not current or not new_pw or new_pw != new_confirm or len(new_pw) < 4:
        return RedirectResponse("/dashboard?tab=status&pwd_error=1", status_code=303)
    if not _login_admin(current):
        return RedirectResponse("/dashboard?tab=status&pwd_error=invalid", status_code=303)
    admin_update_password(new_pw)
    return RedirectResponse("/dashboard?tab=status&pwd_ok=1", status_code=303)


@app.get("/insight")
async def insight(request: Request):
    return templates.TemplateResponse("insight.html", {"request": request})


@app.post("/dashboard/admin/member/add")
async def admin_member_add(request: Request):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    form = await request.form()
    mid = (form.get("member_id") or "").strip()
    if not mid:
        return RedirectResponse("/dashboard?tab=status", status_code=303)
    member_password = form.get("member_password") or ""
    sort_order = None
    so = form.get("member_sort_order")
    if so is not None and so != "":
        try:
            sort_order = int(so)
        except ValueError:
            pass
    member_equity = (form.get("member_equity") or "").strip()
    try:
        member_add_to_db(mid, member_password, sort_order, member_equity)
    except sqlite3.IntegrityError:
        pass
    global MEMBER_DATA, MEMBER_NOTES
    if mid not in MEMBER_DATA:
        MEMBER_DATA[mid] = {y: [False] * 12 for y in _YEARS_TUPLE}
        MEMBER_NOTES[mid] = ""
        save_contrib_to_db(_merge_all_contrib())
        save_notes_to_db(MEMBER_NOTES)
    return RedirectResponse("/dashboard?tab=status", status_code=303)


@app.post("/dashboard/admin/member/edit")
async def admin_member_edit(request: Request):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    form = await request.form()
    member_id = (form.get("member_id") or "").strip()
    member_password = form.get("member_password") or ""
    sort_order = None
    so = form.get("member_sort_order")
    if so is not None and so != "":
        try:
            sort_order = int(so)
        except ValueError:
            pass
    member_equity = (form.get("member_equity") or "").strip()
    member_update_in_db(member_id, member_password, sort_order, member_equity)
    return RedirectResponse("/dashboard?tab=status", status_code=303)


@app.post("/dashboard/admin/member/delete")
async def admin_member_delete(request: Request, member_id: str = Form(...)):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    mid = member_id.strip()
    member_delete_from_db(mid)
    global MEMBER_DATA, MEMBER_NOTES
    MEMBER_DATA.pop(mid, None)
    MEMBER_NOTES.pop(mid, None)
    save_contrib_to_db(_merge_all_contrib())
    save_notes_to_db(MEMBER_NOTES)
    return RedirectResponse("/dashboard?tab=status", status_code=303)


@app.post("/dashboard/admin/partner/add")
async def admin_partner_add(request: Request):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    form = await request.form()
    pid = (form.get("partner_id") or "").strip()
    if not pid:
        return RedirectResponse("/dashboard?tab=status", status_code=303)
    partner_password = form.get("partner_password") or ""
    sort_order = None
    so = form.get("partner_sort_order")
    if so is not None and so != "":
        try:
            sort_order = int(so)
        except ValueError:
            pass
    partner_equity = (form.get("partner_equity") or "").strip()
    try:
        partner_add_to_db(pid, partner_password, sort_order, partner_equity)
    except sqlite3.IntegrityError:
        pass
    global PARTNER_DATA
    if pid not in PARTNER_DATA:
        PARTNER_DATA[pid] = {y: [False] * 12 for y in _YEARS_TUPLE}
        save_contrib_to_db(_merge_all_contrib())
    return RedirectResponse("/dashboard?tab=status", status_code=303)


@app.post("/dashboard/admin/partner/edit")
async def admin_partner_edit(request: Request):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    form = await request.form()
    partner_id = (form.get("partner_id") or "").strip()
    partner_password = form.get("partner_password") or ""
    sort_order = None
    so = form.get("partner_sort_order")
    if so is not None and so != "":
        try:
            sort_order = int(so)
        except ValueError:
            pass
    partner_equity = (form.get("partner_equity") or "").strip()
    partner_update_in_db(partner_id, partner_password, sort_order, partner_equity)
    return RedirectResponse("/dashboard?tab=status", status_code=303)


@app.post("/dashboard/admin/partner/delete")
async def admin_partner_delete(request: Request, partner_id: str = Form(...)):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    pid = partner_id.strip()
    partner_delete_from_db(pid)
    global PARTNER_DATA
    PARTNER_DATA.pop(pid, None)
    save_contrib_to_db(_merge_all_contrib())
    return RedirectResponse("/dashboard?tab=status", status_code=303)


@app.post("/dashboard/admin/backer/add")
async def admin_backer_add(request: Request):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    form = await request.form()
    bid = (form.get("backer_id") or "").strip()
    if not bid:
        return RedirectResponse("/dashboard?tab=status", status_code=303)
    backer_password = form.get("backer_password") or ""
    sort_order = None
    so = form.get("backer_sort_order")
    if so is not None and so != "":
        try:
            sort_order = int(so)
        except ValueError:
            pass
    backer_equity = (form.get("backer_equity") or "").strip()
    try:
        backer_add_to_db(bid, backer_password, sort_order, backer_equity)
    except sqlite3.IntegrityError:
        pass
    global BACKER_DATA
    if bid not in BACKER_DATA:
        BACKER_DATA[bid] = {y: [False] * 12 for y in _YEARS_TUPLE}
        save_contrib_to_db(_merge_all_contrib())
    return RedirectResponse("/dashboard?tab=status", status_code=303)


@app.post("/dashboard/admin/backer/edit")
async def admin_backer_edit(request: Request):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    form = await request.form()
    backer_id = (form.get("backer_id") or "").strip()
    backer_password = form.get("backer_password") or ""
    sort_order = None
    so = form.get("backer_sort_order")
    if so is not None and so != "":
        try:
            sort_order = int(so)
        except ValueError:
            pass
    backer_equity = (form.get("backer_equity") or "").strip()
    backer_update_in_db(backer_id, backer_password, sort_order, backer_equity)
    return RedirectResponse("/dashboard?tab=status", status_code=303)


@app.post("/dashboard/admin/backer/delete")
async def admin_backer_delete(request: Request, backer_id: str = Form(...)):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    bid = backer_id.strip()
    backer_delete_from_db(bid)
    global BACKER_DATA
    BACKER_DATA.pop(bid, None)
    save_contrib_to_db(_merge_all_contrib())
    return RedirectResponse("/dashboard?tab=status", status_code=303)


@app.post("/dashboard/admin/customer/add")
async def admin_customer_add(request: Request):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    form = await request.form()
    cid = (form.get("customer_id") or "").strip()
    if not cid:
        return RedirectResponse("/dashboard?tab=status", status_code=303)
    customer_password = form.get("customer_password") or ""
    sort_order = None
    so = form.get("customer_sort_order")
    if so is not None and so != "":
        try:
            sort_order = int(so)
        except ValueError:
            pass
    customer_equity = (form.get("customer_equity") or "").strip()
    try:
        customer_add_to_db(cid, customer_password, sort_order, customer_equity)
    except sqlite3.IntegrityError:
        pass
    global CUSTOMER_DATA
    if cid not in CUSTOMER_DATA:
        CUSTOMER_DATA[cid] = {y: [False] * 12 for y in _YEARS_TUPLE}
        save_contrib_to_db(_merge_all_contrib())
    return RedirectResponse("/dashboard?tab=status", status_code=303)


@app.post("/dashboard/admin/customer/edit")
async def admin_customer_edit(request: Request):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    form = await request.form()
    customer_id = (form.get("customer_id") or "").strip()
    customer_password = form.get("customer_password") or ""
    sort_order = None
    so = form.get("customer_sort_order")
    if so is not None and so != "":
        try:
            sort_order = int(so)
        except ValueError:
            pass
    customer_equity = (form.get("customer_equity") or "").strip()
    customer_update_in_db(customer_id, customer_password, sort_order, customer_equity)
    return RedirectResponse("/dashboard?tab=status", status_code=303)


@app.post("/dashboard/admin/customer/delete")
async def admin_customer_delete(request: Request, customer_id: str = Form(...)):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    cid = customer_id.strip()
    customer_delete_from_db(cid)
    global CUSTOMER_DATA
    CUSTOMER_DATA.pop(cid, None)
    save_contrib_to_db(_merge_all_contrib())
    return RedirectResponse("/dashboard?tab=status", status_code=303)


# Admin - 멤버 뱃지 추가
@app.post("/dashboard/admin/badge/add")
async def admin_badge_add(request: Request):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    form = await request.form()
    member_id = (form.get("member_id") or "").strip()
    mission_name = (form.get("mission_name") or "").strip()
    if not member_id or not mission_name:
        return RedirectResponse("/dashboard?tab=status", status_code=303)
    try:
        icon_type = int(form.get("icon_type") or "1")
        if icon_type < 1 or icon_type > 10:
            icon_type = 1
    except (ValueError, TypeError):
        icon_type = 1
    badge_add_to_db(member_id, mission_name, icon_type)
    return RedirectResponse("/dashboard?tab=status", status_code=303)


# Admin - 멤버 뱃지 수정
@app.post("/dashboard/admin/badge/edit")
async def admin_badge_edit(request: Request):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    form = await request.form()
    badge_id = form.get("badge_id")
    mission_name = (form.get("mission_name") or "").strip()
    if not badge_id or not mission_name:
        return RedirectResponse("/dashboard?tab=status", status_code=303)
    try:
        bid = int(badge_id)
        icon_type = int(form.get("icon_type") or "1")
        if icon_type < 1 or icon_type > 10:
            icon_type = 1
    except (ValueError, TypeError):
        return RedirectResponse("/dashboard?tab=status", status_code=303)
    badge_update_in_db(bid, mission_name, icon_type)
    return RedirectResponse("/dashboard?tab=status", status_code=303)


# Admin - 멤버 뱃지 삭제
@app.post("/dashboard/admin/badge/delete")
async def admin_badge_delete(request: Request):
    if request.cookies.get("current_user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    form = await request.form()
    badge_id = form.get("badge_id")
    if not badge_id:
        return RedirectResponse("/dashboard?tab=status", status_code=303)
    try:
        bid = int(badge_id)
    except (ValueError, TypeError):
        return RedirectResponse("/dashboard?tab=status", status_code=303)
    badge_delete_from_db(bid)
    return RedirectResponse("/dashboard?tab=status", status_code=303)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
