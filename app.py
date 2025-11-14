import json
import os
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "super-secret-change-this"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(BASE_DIR, "users.json")
EMISSIONS_FILE = os.path.join(BASE_DIR, "emissions.json")
DATA_FILE = os.path.join(BASE_DIR, "data.json")



# ----------------------- USER JSON DB -----------------------

def load_users():
    if not os.path.exists(USERS_FILE):
        return {"users": []}
    try:
        with open(USERS_FILE, "r") as f:
            data = json.load(f)
        if "users" not in data:
            return {"users": []}
        return data
    except Exception:
        return {"users": []}


def save_users(data):
    with open(USERS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def add_user(email, password_hash, name):
    data = load_users()
    users = data["users"]

    next_id = max([u["id"] for u in users], default=0) + 1

    user = {
        "id": next_id,
        "email": email,
        "password_hash": password_hash,
        "name": name,
        "user_type": "pending",
        "created_at": datetime.utcnow().isoformat()
    }
    users.append(user)
    save_users(data)
    return user


def find_user_by_email(email):
    data = load_users()
    for u in data["users"]:
        if u["email"] == email:
            return u
    return None


def update_user_type(user_id, user_type):
    data = load_users()
    for u in data["users"]:
        if u["id"] == user_id:
            u["user_type"] = user_type
            save_users(data)
            return u
    return None


# ----------------------- EMISSIONS JSON DB -----------------------

def load_emissions():
    if not os.path.exists(EMISSIONS_FILE):
        return {"emissions": []}
    try:
        with open(EMISSIONS_FILE, "r") as f:
            data = json.load(f)
        return data
    except Exception:
        return {"emissions": []}


def save_emissions(data):
    with open(EMISSIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def add_emission(user_id, emission_type, amount, unit):
    data = load_emissions()
    emissions = data["emissions"]

    next_id = max([e["id"] for e in emissions], default=0) + 1

    record = {
        "id": next_id,
        "user_id": user_id,
        "type": emission_type,
        "amount": float(amount),
        "unit": unit,
        "created_at": datetime.utcnow().isoformat()
    }
    emissions.append(record)
    save_emissions(data)


def compute_totals_for_user(user_id):
    data = load_emissions()
    totals = {"co2": 0.0, "no2": 0.0, "ch4": 0.0}

    for e in data["emissions"]:
        if e["user_id"] != user_id:
            continue

        etype = e.get("type")
        if etype not in totals:
            continue

        amount = float(e.get("amount", 0.0))
        unit = (e.get("unit") or "").lower()

        # normalise everything into kg
        if unit in ("tonne", "tonnes", "t"):
            amount_kg = amount * 1000.0
        elif unit in ("g", "gram", "grams"):
            amount_kg = amount / 1000.0
        else:  # assume kg
            amount_kg = amount

        totals[etype] += amount_kg

    return totals



# ----------------------- MARKETPLACE DATA (data.json) -----------------------

def load_market_data():
    """Load the big decarbonisation JSON for a single hospital."""
    if not os.path.exists(DATA_FILE):
        return None
    with open(DATA_FILE, "r") as f:
        return json.load(f)


def _iter_items(data):
    """Yield every 'item' dict from the nested levers structure."""
    for lever in data.get("levers", {}).values():
        for sub in lever.get("sub_levers", {}).values():
            for item in sub.get("items", {}).values():
                yield item


def build_leaderboards_from_data(data):
    """
    Returns (leaders_hospitals, leaders_manufacturers) ready for marketplace.html.

    Each element in leaders_* looks like:
    {
      "name": "...",
      "emissions_kg": float,
      "reduction_pct": float,
      "subsidy_pct": float,
      "icon": "🏅"
    }
    All numbers are derived only from data.json (no made-up constants).
    """
    if not data:
        return [], []

    # ---------- Hospital aggregate ----------
    hospital_name = data.get("hospital_profile", {}).get("name", "Unknown Hospital")

    total_tonnes = 0.0
    total_cost_current = 0.0
    total_cost_alt = 0.0

    for item in _iter_items(data):
        carbon = item.get("carbon", {})
        costing = item.get("costing", {})

        total_tonnes += float(carbon.get("annual_co2e_tonnes", 0.0))
        total_cost_current += float(costing.get("annual_cost_rupees", 0.0))
        total_cost_alt += float(costing.get("annual_alternative_cost_rupees", 0.0))

    # cost-based reduction %
    if total_cost_current > 0:
        reduction_pct = (total_cost_current - total_cost_alt) / total_cost_current * 100.0
    else:
        reduction_pct = 0.0

    # derive subsidy as a simple function of real reduction %
    subsidy_pct_hosp = max(0.0, min(40.0, round(reduction_pct, 1)))

    leaders_hospitals = [{
        "name": hospital_name,
        "emissions_kg": total_tonnes * 1000.0,  # tonnes -> kg
        "reduction_pct": reduction_pct,
        "subsidy_pct": subsidy_pct_hosp,
        "icon": "🏅",
    }]

    # ---------- Manufacturer aggregates ----------
    manufacturers = {}  # name -> accumulator dict

    for item in _iter_items(data):
        carbon = item.get("carbon", {})
        costing = item.get("costing", {})
        tonnes = float(carbon.get("annual_co2e_tonnes", 0.0))
        cost_cur = float(costing.get("annual_cost_rupees", 0.0))
        cost_alt = float(costing.get("annual_alternative_cost_rupees", 0.0))

        for supplier in item.get("sourcing", {}).get("suppliers", []):
            m = manufacturers.setdefault(
                supplier,
                {"tonnes": 0.0, "cost_cur": 0.0, "cost_alt": 0.0},
            )
            m["tonnes"] += tonnes
            m["cost_cur"] += cost_cur
            m["cost_alt"] += cost_alt

    leaders_manufacturers = []
    for name, agg in manufacturers.items():
        if agg["cost_cur"] > 0:
            red_pct = (agg["cost_cur"] - agg["cost_alt"]) / agg["cost_cur"] * 100.0
        else:
            red_pct = 0.0
        subsidy_pct = max(0.0, min(40.0, round(red_pct, 1)))

        leaders_manufacturers.append({
            "name": name,
            "emissions_kg": agg["tonnes"] * 1000.0,
            "reduction_pct": red_pct,
            "subsidy_pct": subsidy_pct,
            "icon": "🏅",
        })

    # Sort: lowest emissions first
    leaders_hospitals.sort(key=lambda x: x["emissions_kg"])
    leaders_manufacturers.sort(key=lambda x: x["emissions_kg"])

    return leaders_hospitals, leaders_manufacturers



# ------------------- MARKETPLACE LEADERBOARD FROM EMISSIONS.JSON -------------------

def build_leaderboards_from_emissions():
    """
    Build hospital + manufacturer leaderboards from real logged emissions.

    Only two user types are considered:
      - "hospital"
      - "manufacturer"

    Any other user_type will be treated as "hospital" (so old users still show up).
    """
    users_data = load_users()
    all_users = users_data.get("users", [])

    hospital_entries = []
    manufacturer_entries = []

    for u in all_users:
        raw_type = (u.get("user_type") or "").lower().strip()

        # compute totals first – if they have no emissions, skip
        totals = compute_totals_for_user(u["id"])  # in kg
        total_kg = float(totals.get("co2", 0.0)) + float(totals.get("no2", 0.0)) + float(totals.get("ch4", 0.0))
        if total_kg <= 0:
            continue

        # normalise type: anything unknown -> hospital
        if raw_type not in ("hospital", "manufacturer"):
            raw_type = "hospital"

        entry = {
            "name": u.get("name") or u.get("email"),
            "emissions_kg": total_kg,
            "reduction_pct": 0.0,  # set later
            "subsidy_pct": 0.0,    # set later
            "icon": "🏅",
        }

        if raw_type == "hospital":
            hospital_entries.append(entry)
        elif raw_type == "manufacturer":
            manufacturer_entries.append(entry)

    def finalize_group(entries):
        if not entries:
            return entries

        max_kg = max(e["emissions_kg"] for e in entries)
        if max_kg <= 0:
            return entries

        for e in entries:
            # relative "reduction" vs worst emitter
            rel = (max_kg - e["emissions_kg"]) / max_kg * 100.0
            e["reduction_pct"] = rel
            e["subsidy_pct"] = round(max(0.0, min(40.0, rel)), 1)

        # sort best (lowest emissions) to worst
        entries.sort(key=lambda x: x["emissions_kg"])
        return entries

    hospital_entries = finalize_group(hospital_entries)
    manufacturer_entries = finalize_group(manufacturer_entries)

    return hospital_entries, manufacturer_entries

# ----------------------- ROUTES -----------------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():

    # USER SUBMITS LOGIN FORM
    if request.method == "POST":
        email = request.form.get("email").strip().lower()
        password = request.form.get("password")

        user = find_user_by_email(email)
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            session["user_type"] = user["user_type"]
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")
        return redirect(url_for("login"))

    return render_template("login.html", mode="auth")


@app.route("/signup", methods=["POST"])
def signup():
    # Safely read fields
    email = (request.form.get("email") or "").strip().lower()
    password = (request.form.get("password") or "")
    name = (request.form.get("name") or "").strip()

    # Basic validation – if anything is missing, bounce back
    if not email or not password or not name:
        flash("Please fill in email, password, and name.", "error")
        return redirect(url_for("login"))

    # Check if user already exists
    if find_user_by_email(email):
        flash("Account already exists.", "error")
        return redirect(url_for("login"))

    # Create user
    hashed = generate_password_hash(password)
    new_user = add_user(email, hashed, name)

    # Store pending identity in session
    session["pending_user_id"] = new_user["id"]
    session["pending_user_email"] = new_user["email"]

    # Go to identity selection (hospital / manufacturer)
    return redirect(url_for("identify"))


@app.route("/identify", methods=["GET"])
def identify():
    if "pending_user_id" not in session:
        return redirect(url_for("login"))
    return render_template("login.html", mode="identify")


@app.route("/set_identity", methods=["POST"])
def set_identity():
    user_type = request.form.get("user_type")
    uid = session.get("pending_user_id")

    update_user_type(uid, user_type)

    session["user_id"] = uid
    session["user_email"] = session["pending_user_email"]
    session["user_type"] = user_type

    session.pop("pending_user_id", None)
    session.pop("pending_user_email", None)

    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    totals = compute_totals_for_user(session["user_id"])

    return render_template(
        "dashboard.html",
        totals=totals,
        user_email=session.get("user_email")
    )


@app.route("/add_emission", methods=["POST"])
def add_emission_route():
    if "user_id" not in session:
        return redirect(url_for("login"))

    emission_type = request.form.get("emission_type")
    amount = request.form.get("amount")
    unit = request.form.get("unit")

    add_emission(session["user_id"], emission_type, amount, unit)

    return redirect(url_for("dashboard"))


@app.route("/marketplace")
def marketplace():
    leaders_hospitals, leaders_manufacturers = build_leaderboards_from_emissions()

    return render_template(
        "marketplace.html",
        leaders_hospitals=leaders_hospitals,
        leaders_manufacturers=leaders_manufacturers,
    )




@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True)
