"""
Job Search Bot — Flask API
Deployed on Render. Connects to Neon Postgres.
Routes:
  GET  /health              — health check
  GET  /professions         — list all professions (for autocomplete)
  GET  /professions/<id>/variants — variants for a profession
  POST /register            — register a new user
  PUT  /user/<email>        — update existing user
  GET  /user/<email>        — get user data (for edit form)
"""

import os
import psycopg2
import psycopg2.extras
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)  # allow requests from your index.html form


# ── DB connection ─────────────────────────────────────────────

def get_db():
    return psycopg2.connect(
        os.environ["DATABASE_URL"],
        cursor_factory=psycopg2.extras.RealDictCursor
    )


# ── Health check ──────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ── GET /professions ──────────────────────────────────────────

@app.route("/professions")
def get_professions():
    """Return all professions for autocomplete dropdown."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, profession FROM profession_t ORDER BY profession")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── GET /professions/<id>/variants ────────────────────────────

@app.route("/professions/<int:profession_id>/variants")
def get_variants(profession_id):
    """Return known variants for a given profession."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT variant FROM variant_t WHERE profession_id = %s ORDER BY variant",
            (profession_id,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([r["variant"] for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── GET /user/<email> ─────────────────────────────────────────

@app.route("/user/<email>")
def get_user(email):
    """Return a user's full data for the edit form."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM v_users WHERE email = %s", (email.lower(),))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return jsonify({"error": "User not found"}), 404
        return jsonify(dict(row))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── POST /register ────────────────────────────────────────────

@app.route("/register", methods=["POST"])
def register():
    """Register a new user from the form."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    required = ["first_name", "last_name", "email", "profession"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Missing required field: {field}"}), 400

    try:
        conn = get_db()
        cur = conn.cursor()

        email = data["email"].lower().strip()

        # Check duplicate
        cur.execute("SELECT id FROM user_t WHERE email = %s", (email,))
        if cur.fetchone():
            cur.close()
            conn.close()
            return jsonify({"error": "Email already registered"}), 409

        # Work type
        work_type_id = _get_or_none(cur, "work_type_t", "work_type", data.get("work_type"))

        # Insert user
        cur.execute(
            """INSERT INTO user_t (first_name, last_name, email, work_type_id)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (
                data["first_name"].strip().title(),
                data["last_name"].strip().title(),
                email,
                work_type_id,
            )
        )
        user_id = cur.fetchone()["id"]

        # Professions (list, ordered by priority)
        professions = data.get("professions") or [data.get("profession")]
        professions = [p for p in professions if p]
        for i, prof_name in enumerate(professions[:3]):
            prof_id = _get_or_create_profession(cur, prof_name)
            cur.execute(
                """INSERT INTO user_profession_t (user_id, profession_id, priority)
                   VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                (user_id, prof_id, i + 1)
            )

        # Variants (free text)
        for variant in (data.get("variants") or []):
            if variant.strip():
                cur.execute(
                    """INSERT INTO user_variant_t (user_id, variant)
                       VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                    (user_id, variant.strip())
                )

        # Seniority
        _insert_junction_lookup(cur, user_id, "seniority_t", "seniority",
                                 "user_seniority_t", "seniority_id",
                                 data.get("seniority") or [])

        # Company type
        _insert_junction_lookup(cur, user_id, "company_type_t", "company_type",
                                 "user_company_type_t", "company_type_id",
                                 data.get("company_type") or [])

        # Cities
        for city_name in (data.get("city") or []):
            cur.execute("SELECT id FROM location_t WHERE city = %s", (city_name,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    """INSERT INTO user_location_t (user_id, location_id)
                       VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                    (user_id, row["id"])
                )

        # Websites
        for website in (data.get("websites") or []):
            cur.execute("SELECT id FROM website_t WHERE website = %s", (website,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    """INSERT INTO user_website_t (user_id, website_id)
                       VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                    (user_id, row["id"])
                )

        # Frequency
        for slot in (data.get("frequency") or []):
            cur.execute("SELECT id FROM frequency_t WHERE time_slot = %s", (slot,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    """INSERT INTO user_frequency_t (user_id, frequency_id)
                       VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                    (user_id, row["id"])
                )

        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"message": "User registered successfully", "user_id": user_id}), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── PUT /user/<email> ─────────────────────────────────────────

@app.route("/user/<email>", methods=["PUT"])
def update_user(email):
    """Update an existing user — replaces all junction table entries."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    try:
        conn = get_db()
        cur = conn.cursor()

        email = email.lower().strip()
        cur.execute("SELECT id FROM user_t WHERE email = %s", (email,))
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return jsonify({"error": "User not found"}), 404

        user_id = row["id"]

        # Update core fields
        work_type_id = _get_or_none(cur, "work_type_t", "work_type", data.get("work_type"))
        cur.execute(
            """UPDATE user_t SET
                first_name   = COALESCE(%s, first_name),
                last_name    = COALESCE(%s, last_name),
                work_type_id = COALESCE(%s, work_type_id)
               WHERE id = %s""",
            (
                data.get("first_name", "").strip().title() or None,
                data.get("last_name", "").strip().title() or None,
                work_type_id,
                user_id,
            )
        )

        # Clear and re-insert junction tables
        for table in [
            "user_profession_t", "user_variant_t", "user_seniority_t",
            "user_company_type_t", "user_location_t", "user_website_t", "user_frequency_t"
        ]:
            cur.execute(f"DELETE FROM {table} WHERE user_id = %s", (user_id,))

        # Re-insert everything (same logic as register)
        professions = data.get("professions") or [data.get("profession")]
        professions = [p for p in professions if p]
        for i, prof_name in enumerate(professions[:3]):
            prof_id = _get_or_create_profession(cur, prof_name)
            cur.execute(
                """INSERT INTO user_profession_t (user_id, profession_id, priority)
                   VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                (user_id, prof_id, i + 1)
            )

        for variant in (data.get("variants") or []):
            if variant.strip():
                cur.execute(
                    "INSERT INTO user_variant_t (user_id, variant) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (user_id, variant.strip())
                )

        _insert_junction_lookup(cur, user_id, "seniority_t", "seniority",
                                 "user_seniority_t", "seniority_id",
                                 data.get("seniority") or [])

        _insert_junction_lookup(cur, user_id, "company_type_t", "company_type",
                                 "user_company_type_t", "company_type_id",
                                 data.get("company_type") or [])

        for city_name in (data.get("city") or []):
            cur.execute("SELECT id FROM location_t WHERE city = %s", (city_name,))
            r = cur.fetchone()
            if r:
                cur.execute(
                    "INSERT INTO user_location_t (user_id, location_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (user_id, r["id"])
                )

        for website in (data.get("websites") or []):
            cur.execute("SELECT id FROM website_t WHERE website = %s", (website,))
            r = cur.fetchone()
            if r:
                cur.execute(
                    "INSERT INTO user_website_t (user_id, website_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (user_id, r["id"])
                )

        for slot in (data.get("frequency") or []):
            cur.execute("SELECT id FROM frequency_t WHERE time_slot = %s", (slot,))
            r = cur.fetchone()
            if r:
                cur.execute(
                    "INSERT INTO user_frequency_t (user_id, frequency_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (user_id, r["id"])
                )

        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"message": "User updated successfully"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Helpers ───────────────────────────────────────────────────

def _get_or_none(cur, table, column, value):
    """Return the ID of a lookup row, or None if value is empty."""
    if not value:
        return None
    cur.execute(f"SELECT id FROM {table} WHERE {column} = %s", (value,))
    row = cur.fetchone()
    return row["id"] if row else None


def _get_or_create_profession(cur, profession_name: str) -> int:
    """Get or insert a profession (Title Case). Also returns its ID."""
    name = profession_name.strip().title()
    cur.execute("SELECT id FROM profession_t WHERE profession = %s", (name,))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur.execute(
        "INSERT INTO profession_t (profession) VALUES (%s) RETURNING id", (name,)
    )
    return cur.fetchone()["id"]


def _insert_junction_lookup(cur, user_id, lookup_table, lookup_col,
                             junction_table, fk_col, values: list):
    """Generic helper to insert many-to-many rows for lookup tables."""
    for val in values:
        cur.execute(f"SELECT id FROM {lookup_table} WHERE {lookup_col} = %s", (val,))
        row = cur.fetchone()
        if row:
            cur.execute(
                f"INSERT INTO {junction_table} (user_id, {fk_col}) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (user_id, row["id"])
            )


# ── Run ───────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
