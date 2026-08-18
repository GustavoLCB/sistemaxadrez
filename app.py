"""
Portal do Torneio de Xadrez Escolar — Backend Flask
Versão com autenticação: categorias, atletas, grupos, rodadas suíças, Elo,
resultados, classificação, login por fiscal (restrito ao(s) grupo(s) dele)
e um endpoint público de leitura para o quadro público (sem login).
"""
import os
import secrets
import io
from functools import wraps
from flask import Flask, request, jsonify, render_template, session, redirect, send_file
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode
from db import init_db, write_transaction, read_conn
from logic import (new_id, k_for, points_for, opponents_of, had_bye, has_played,
                    pair_by_score_brackets, assign_colors, compute_elo_update,
                    normalize_cpf, is_valid_cpf)

app = Flask(__name__)
init_db()

# ---------- chave de sessão (gerada uma vez, guardada em arquivo local — nunca no git) ----------
SECRET_KEY_PATH = os.path.join(os.path.dirname(__file__), "secret_key.txt")
def _get_or_create_secret_key():
    if os.path.exists(SECRET_KEY_PATH):
        return open(SECRET_KEY_PATH).read().strip()
    key = secrets.token_hex(32)
    with open(SECRET_KEY_PATH, "w") as f:
        f.write(key)
    return key
app.secret_key = _get_or_create_secret_key()


# ---------- autenticação / autorização ----------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "Sessão expirada, faça login novamente"}), 401
        return view(*args, **kwargs)
    return wrapped

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != "admin":
            return jsonify({"error": "Ação restrita ao administrador"}), 403
        return view(*args, **kwargs)
    return wrapped

def user_group_ids(conn):
    """Grupos que o usuário logado pode acessar. Admin: todos. Fiscal: só os atribuídos a ele."""
    if session.get("role") == "admin":
        return [r["id"] for r in conn.execute("SELECT id FROM groups_t").fetchall()]
    uid = session.get("user_id")
    if not uid:
        return []
    rows = conn.execute("SELECT group_id FROM user_groups WHERE user_id=?", (uid,)).fetchall()
    return [r["group_id"] for r in rows]

def can_access_group(conn, group_id):
    if session.get("role") == "admin":
        return True
    return group_id in user_group_ids(conn)


# ---------- helpers ----------

def row_to_dict(row):
    return dict(row) if row else None

def group_matches_by_round(conn, group_id):
    """Retorna lista de listas: cada sublista é os matches de uma rodada, em ordem."""
    rounds = conn.execute(
        "SELECT * FROM rounds WHERE group_id=? ORDER BY round_number", (group_id,)
    ).fetchall()
    out = []
    for r in rounds:
        matches = conn.execute("SELECT * FROM matches WHERE round_id=?", (r["id"],)).fetchall()
        out.append([{
            "id": m["id"], "whiteId": m["white_id"], "blackId": m["black_id"],
            "isBye": bool(m["is_bye"]), "byeAthleteId": m["bye_athlete_id"],
            "result": m["result"], "roundNumber": r["round_number"], "roundId": r["id"],
        } for m in matches])
    return out

def flatten(matches_by_round):
    return [m for rnd in matches_by_round for m in rnd]


# ---------- páginas ----------

@app.route("/")
def index():
    if not session.get("user_id"):
        return redirect("/login")
    if session.get("role") == "fiscal":
        return render_template("fiscal.html")
    return render_template("index.html")

@app.route("/login", methods=["GET"])
def login_page():
    if session.get("user_id"):
        return redirect("/")
    return render_template("login.html")

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    with read_conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Usuário ou senha incorretos"}), 401
    session.clear()
    session["user_id"] = user["id"]
    session["role"] = user["role"]
    session["full_name"] = user["full_name"]
    session.permanent = True
    return jsonify({"ok": True, "role": user["role"], "fullName": user["full_name"]})

@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/me", methods=["GET"])
def api_me():
    if not session.get("user_id"):
        return jsonify({"error": "não autenticado"}), 401
    with read_conn() as conn:
        gids = user_group_ids(conn)
        groups = []
        if gids:
            placeholders = ",".join("?" * len(gids))
            rows = conn.execute(f"SELECT * FROM groups_t WHERE id IN ({placeholders})", gids).fetchall()
            groups = [row_to_dict(r) for r in rows]
    return jsonify({"role": session.get("role"), "fullName": session.get("full_name"), "groups": groups})

@app.route("/publico/<group_id>")
def publico(group_id):
    return render_template("publico.html", group_id=group_id)


# ---------- usuários (fiscais) — só admin ----------

@app.route("/api/users", methods=["GET"])
@login_required
@admin_required
def list_users():
    with read_conn() as conn:
        rows = conn.execute("SELECT id, username, full_name, role FROM users ORDER BY full_name").fetchall()
        users = []
        for r in rows:
            gids = [g["group_id"] for g in conn.execute(
                "SELECT group_id FROM user_groups WHERE user_id=?", (r["id"],)).fetchall()]
            u = row_to_dict(r)
            u["groupIds"] = gids
            users.append(u)
        return jsonify(users)

@app.route("/api/users", methods=["POST"])
@login_required
@admin_required
def create_user():
    data = request.get_json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    full_name = (data.get("fullName") or "").strip()
    role = data.get("role", "fiscal")
    group_ids = data.get("groupIds", [])
    if not username or not password or len(password) < 4:
        return jsonify({"error": "Usuário e senha (mín. 4 caracteres) são obrigatórios"}), 400
    with write_transaction() as conn:
        existing = conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
        if existing:
            return jsonify({"error": "Esse nome de usuário já existe"}), 400
        uid = new_id()
        conn.execute("INSERT INTO users (id, username, password_hash, full_name, role) VALUES (?,?,?,?,?)",
                     (uid, username, generate_password_hash(password), full_name, role))
        for gid in group_ids:
            conn.execute("INSERT OR IGNORE INTO user_groups (user_id, group_id) VALUES (?,?)", (uid, gid))
        return jsonify({"id": uid})

@app.route("/api/users/<uid>", methods=["PATCH"])
@login_required
@admin_required
def update_user(uid):
    data = request.get_json()
    with write_transaction() as conn:
        if "password" in data and data["password"]:
            if len(data["password"]) < 4:
                return jsonify({"error": "Senha muito curta"}), 400
            conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                         (generate_password_hash(data["password"]), uid))
        if "fullName" in data:
            conn.execute("UPDATE users SET full_name=? WHERE id=?", (data["fullName"], uid))
        if "groupIds" in data:
            conn.execute("DELETE FROM user_groups WHERE user_id=?", (uid,))
            for gid in data["groupIds"]:
                conn.execute("INSERT OR IGNORE INTO user_groups (user_id, group_id) VALUES (?,?)", (uid, gid))
        return jsonify({"ok": True})

@app.route("/api/users/<uid>", methods=["DELETE"])
@login_required
@admin_required
def delete_user(uid):
    with write_transaction() as conn:
        conn.execute("DELETE FROM user_groups WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
        return jsonify({"ok": True})


# ---------- categorias ----------

@app.route("/api/categories", methods=["GET"])
@login_required
def list_categories():
    with read_conn() as conn:
        rows = conn.execute("SELECT * FROM categories ORDER BY name").fetchall()
        return jsonify([row_to_dict(r) for r in rows])

@app.route("/api/categories", methods=["POST"])
@login_required
@admin_required
def create_category():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Nome obrigatório"}), 400
    with write_transaction() as conn:
        existing = conn.execute("SELECT * FROM categories WHERE name=?", (name,)).fetchone()
        if existing:
            return jsonify(row_to_dict(existing))
        cid = new_id()
        conn.execute("INSERT INTO categories (id, name) VALUES (?,?)", (cid, name))
        return jsonify({"id": cid, "name": name})

@app.route("/api/categories/<cid>", methods=["DELETE"])
@login_required
@admin_required
def delete_category(cid):
    with write_transaction() as conn:
        in_use = conn.execute("SELECT COUNT(*) c FROM athletes WHERE category_id=?", (cid,)).fetchone()["c"]
        in_use += conn.execute("SELECT COUNT(*) c FROM groups_t WHERE category_id=?", (cid,)).fetchone()["c"]
        if in_use:
            return jsonify({"error": "Categoria em uso por atletas ou grupos"}), 400
        conn.execute("DELETE FROM category_rules WHERE category_id=?", (cid,))
        conn.execute("DELETE FROM categories WHERE id=?", (cid,))
        return jsonify({"ok": True})


# ---------- regras de idade -> categoria ----------

@app.route("/api/category-rules", methods=["GET"])
@login_required
def list_rules():
    with read_conn() as conn:
        rows = conn.execute("SELECT * FROM category_rules").fetchall()
        return jsonify([row_to_dict(r) for r in rows])

@app.route("/api/category-rules", methods=["POST"])
@login_required
@admin_required
def create_rule():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    min_age, max_age = data.get("minAge"), data.get("maxAge")
    if not name or min_age is None or max_age is None:
        return jsonify({"error": "Preencha nome e as duas idades"}), 400
    min_age, max_age = int(min_age), int(max_age)
    if min_age > max_age:
        return jsonify({"error": "Idade mínima maior que a máxima"}), 400
    with write_transaction() as conn:
        overlap = conn.execute(
            "SELECT cr.*, c.name as cat_name FROM category_rules cr JOIN categories c ON c.id=cr.category_id "
            "WHERE ? <= cr.max_age AND cr.min_age <= ?", (min_age, max_age)
        ).fetchone()
        if overlap:
            return jsonify({"error": f'Faixa se sobrepõe com "{overlap["cat_name"]}" ({overlap["min_age"]}-{overlap["max_age"]})'}), 400
        cat = conn.execute("SELECT * FROM categories WHERE name=?", (name,)).fetchone()
        if not cat:
            cat_id = new_id()
            conn.execute("INSERT INTO categories (id, name) VALUES (?,?)", (cat_id, name))
        else:
            cat_id = cat["id"]
        rule_id = new_id()
        conn.execute("INSERT INTO category_rules (id, category_id, min_age, max_age) VALUES (?,?,?,?)",
                     (rule_id, cat_id, min_age, max_age))
        return jsonify({"id": rule_id, "categoryId": cat_id, "minAge": min_age, "maxAge": max_age})

@app.route("/api/category-rules/<rid>", methods=["DELETE"])
@login_required
@admin_required
def delete_rule(rid):
    with write_transaction() as conn:
        conn.execute("DELETE FROM category_rules WHERE id=?", (rid,))
        return jsonify({"ok": True})


# ---------- grupos ----------

@app.route("/api/groups", methods=["GET"])
@login_required
def list_groups():
    with read_conn() as conn:
        gids = user_group_ids(conn)
        if not gids:
            return jsonify([])
        placeholders = ",".join("?" * len(gids))
        rows = conn.execute(
            f"""SELECT g.*, c.name as category_name FROM groups_t g
                JOIN categories c ON c.id = g.category_id
                WHERE g.id IN ({placeholders})
                ORDER BY c.name, g.gender, g.name""", gids
        ).fetchall()
        return jsonify([row_to_dict(r) for r in rows])

@app.route("/api/groups", methods=["POST"])
@login_required
@admin_required
def create_group():
    data = request.get_json()
    category_id, gender, name = data.get("categoryId"), data.get("gender"), (data.get("name") or "").strip()
    max_rounds = data.get("maxRounds")
    max_rounds = int(max_rounds) if max_rounds not in (None, "") else None
    if not (category_id and gender in ("M", "F") and name):
        return jsonify({"error": "Dados incompletos"}), 400
    with write_transaction() as conn:
        gid = new_id()
        conn.execute("INSERT INTO groups_t (id, category_id, gender, name, max_rounds) VALUES (?,?,?,?,?)",
                     (gid, category_id, gender, name, max_rounds))
        return jsonify({"id": gid, "categoryId": category_id, "gender": gender, "name": name, "maxRounds": max_rounds})

@app.route("/api/groups/<gid>", methods=["PATCH"])
@login_required
@admin_required
def update_group(gid):
    data = request.get_json()
    with write_transaction() as conn:
        if "name" in data:
            conn.execute("UPDATE groups_t SET name=? WHERE id=?", (data["name"], gid))
        if "maxRounds" in data:
            mr = data["maxRounds"]
            mr = int(mr) if mr not in (None, "") else None
            conn.execute("UPDATE groups_t SET max_rounds=? WHERE id=?", (mr, gid))
        return jsonify({"ok": True})

@app.route("/api/groups/<gid>", methods=["DELETE"])
@login_required
@admin_required
def delete_group(gid):
    with write_transaction() as conn:
        in_use = conn.execute("SELECT COUNT(*) c FROM athletes WHERE group_id=?", (gid,)).fetchone()["c"]
        rounds_count = conn.execute("SELECT COUNT(*) c FROM rounds WHERE group_id=?", (gid,)).fetchone()["c"]
        if in_use or rounds_count:
            return jsonify({"error": "Grupo em uso por atletas ou rodadas"}), 400
        conn.execute("DELETE FROM groups_t WHERE id=?", (gid,))
        return jsonify({"ok": True})

@app.route("/api/groups/auto-split", methods=["POST"])
@login_required
@admin_required
def auto_split_groups():
    """Divide em N grupos por seed de cobra (equilibra rating médio)."""
    data = request.get_json()
    category_id, gender, n = data.get("categoryId"), data.get("gender"), int(data.get("n", 2))
    names = data.get("names")
    n = max(1, min(30, n))
    with write_transaction() as conn:
        pool = conn.execute(
            "SELECT * FROM athletes WHERE category_id=? AND gender=? AND group_id IS NULL",
            (category_id, gender)
        ).fetchall()
        pool = sorted(pool, key=lambda a: -a["rating"])
        if len(pool) < n:
            return jsonify({"error": f"É preciso ao menos {n} atletas sem grupo (há {len(pool)})"}), 400
        buckets = [[] for _ in range(n)]
        direction, col = 1, 0
        for a in pool:
            buckets[col].append(a)
            if direction == 1 and col == n - 1:
                direction = -1
            elif direction == -1 and col == 0:
                direction = 1
            else:
                col += direction
        group_names = names if (names and len(names) == n) else [f"Grupo {chr(65+i)}" for i in range(n)]
        summary = []
        for i, bucket in enumerate(buckets):
            if not bucket:
                continue
            existing = conn.execute(
                "SELECT * FROM groups_t WHERE category_id=? AND gender=? AND name=?",
                (category_id, gender, group_names[i])
            ).fetchone()
            if existing:
                gid = existing["id"]
            else:
                gid = new_id()
                conn.execute("INSERT INTO groups_t (id, category_id, gender, name) VALUES (?,?,?,?)",
                             (gid, category_id, gender, group_names[i]))
            for a in bucket:
                conn.execute("UPDATE athletes SET group_id=? WHERE id=?", (gid, a["id"]))
            summary.append(f"{group_names[i]}: {len(bucket)}")
        return jsonify({"ok": True, "summary": summary})


# ---------- atletas ----------

@app.route("/api/athletes", methods=["GET"])
@login_required
def list_athletes():
    with read_conn() as conn:
        if session.get("role") == "admin":
            rows = conn.execute("SELECT * FROM athletes ORDER BY full_name").fetchall()
        else:
            gids = user_group_ids(conn)
            if not gids:
                return jsonify([])
            placeholders = ",".join("?" * len(gids))
            rows = conn.execute(
                f"SELECT * FROM athletes WHERE group_id IN ({placeholders}) ORDER BY full_name", gids
            ).fetchall()
        return jsonify([row_to_dict(r) for r in rows])

@app.route("/api/athletes", methods=["POST"])
@login_required
@admin_required
def create_athlete():
    data = request.get_json()
    full_name = (data.get("fullName") or "").strip()
    if not full_name or not data.get("categoryId"):
        return jsonify({"error": "Nome e categoria são obrigatórios"}), 400
    cpf = normalize_cpf(data.get("cpf", ""))
    if cpf and not is_valid_cpf(cpf):
        return jsonify({"error": "CPF inválido — confira os números digitados"}), 400
    with write_transaction() as conn:
        aid = data.get("id") or new_id()
        if cpf:
            dup = conn.execute("SELECT full_name FROM athletes WHERE cpf=? AND id!=?", (cpf, aid)).fetchone()
            if dup:
                return jsonify({"error": f'Este CPF já está cadastrado para "{dup["full_name"]}"'}), 400
        conn.execute("""INSERT INTO athletes (id, full_name, gender, category_id, group_id, k_flag, rating, age, school, cpf)
                         VALUES (?,?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(id) DO UPDATE SET full_name=excluded.full_name, gender=excluded.gender,
                            category_id=excluded.category_id, k_flag=excluded.k_flag, rating=excluded.rating,
                            age=excluded.age, school=excluded.school, cpf=excluded.cpf""",
                     (aid, full_name, data.get("gender", "M"), data["categoryId"], data.get("groupId"),
                      data.get("kFlag", "iniciante"), int(data.get("rating", 1500)), data.get("age"),
                      data.get("school", ""), cpf or None))
        return jsonify({"id": aid})

@app.route("/api/athletes/<aid>", methods=["PATCH"])
@login_required
@admin_required
def update_athlete(aid):
    data = request.get_json()
    if "cpf" in data:
        cpf = normalize_cpf(data["cpf"])
        if cpf and not is_valid_cpf(cpf):
            return jsonify({"error": "CPF inválido — confira os números digitados"}), 400
        data["cpf"] = cpf or None
    fields, values = [], []
    for key, col in [("fullName", "full_name"), ("gender", "gender"), ("categoryId", "category_id"),
                      ("groupId", "group_id"), ("kFlag", "k_flag"), ("rating", "rating"),
                      ("age", "age"), ("school", "school"), ("cpf", "cpf")]:
        if key in data:
            fields.append(f"{col}=?")
            values.append(data[key])
    if not fields:
        return jsonify({"ok": True})
    values.append(aid)
    with write_transaction() as conn:
        if data.get("cpf"):
            dup = conn.execute("SELECT full_name FROM athletes WHERE cpf=? AND id!=?", (data["cpf"], aid)).fetchone()
            if dup:
                return jsonify({"error": f'Este CPF já está cadastrado para "{dup["full_name"]}"'}), 400
        conn.execute(f"UPDATE athletes SET {', '.join(fields)} WHERE id=?", values)
        return jsonify({"ok": True})

@app.route("/api/athletes/<aid>", methods=["DELETE"])
@login_required
@admin_required
def delete_athlete(aid):
    with write_transaction() as conn:
        in_match = conn.execute(
            "SELECT COUNT(*) c FROM matches WHERE white_id=? OR black_id=? OR bye_athlete_id=?",
            (aid, aid, aid)
        ).fetchone()["c"]
        if in_match:
            return jsonify({"error": "Atleta já possui partidas registradas"}), 400
        conn.execute("DELETE FROM athletes WHERE id=?", (aid,))
        return jsonify({"ok": True})

@app.route("/api/athletes/import", methods=["POST"])
@login_required
@admin_required
def import_athletes():
    """Recebe uma lista JSON já parseada no navegador (nome, idade, colégio, gênero, CPF
       opcionais) e classifica pela idade usando as category_rules cadastradas.
       Quando há CPF, ele é a chave de deduplicação (mais confiável que nome+categoria,
       que pode falhar por acentuação, apelido, homônimos etc.)."""
    data = request.get_json()
    rows = data.get("rows", [])
    default_gender = data.get("defaultGender", "M")
    k_flag = data.get("kFlag", "iniciante")
    default_rating = int(data.get("rating", 1500))
    group_size = max(1, int(data.get("groupSize", 25)))

    added, skipped, unmatched, invalid_cpf = 0, 0, 0, 0
    touched = set()
    with write_transaction() as conn:
        rules = conn.execute(
            "SELECT cr.*, c.name as cat_name FROM category_rules cr JOIN categories c ON c.id=cr.category_id"
        ).fetchall()
        fallback = conn.execute("SELECT * FROM categories WHERE name=?",
                                 ("Sem categoria (idade fora das faixas)",)).fetchone()
        for r in rows:
            full_name = (r.get("fullName") or "").strip()
            if not full_name:
                continue
            gender = r.get("gender") or default_gender
            age = r.get("age")
            school = r.get("school") or ""
            cpf = normalize_cpf(r.get("cpf", ""))
            if cpf and not is_valid_cpf(cpf):
                invalid_cpf += 1
                cpf = ""  # não bloqueia a linha inteira, só ignora o CPF inválido
            category_id = None
            if age is not None:
                for rule in rules:
                    if rule["min_age"] <= age <= rule["max_age"]:
                        category_id = rule["category_id"]
                        break
            if category_id is None:
                if not fallback:
                    fid = new_id()
                    conn.execute("INSERT INTO categories (id, name) VALUES (?,?)",
                                 (fid, "Sem categoria (idade fora das faixas)"))
                    fallback = {"id": fid}
                category_id = fallback["id"] if isinstance(fallback, dict) else fallback["id"]
                unmatched += 1

            if cpf:
                dup = conn.execute("SELECT COUNT(*) c FROM athletes WHERE cpf=?", (cpf,)).fetchone()["c"]
            else:
                dup = conn.execute(
                    "SELECT COUNT(*) c FROM athletes WHERE lower(full_name)=? AND category_id=?",
                    (full_name.lower(), category_id)
                ).fetchone()["c"]
            if dup:
                skipped += 1
                continue
            aid = new_id()
            conn.execute("""INSERT INTO athletes (id, full_name, gender, category_id, k_flag, rating, age, school, cpf)
                             VALUES (?,?,?,?,?,?,?,?,?)""",
                         (aid, full_name, gender, category_id, k_flag, default_rating, age, school, cpf or None))
            added += 1
            touched.add((category_id, gender))

        groups_created = 0
        for category_id, gender in touched:
            existing = conn.execute(
                "SELECT COUNT(*) c FROM groups_t WHERE category_id=? AND gender=?", (category_id, gender)
            ).fetchone()["c"]
            if existing:
                continue
            pool = conn.execute(
                "SELECT * FROM athletes WHERE category_id=? AND gender=? AND group_id IS NULL",
                (category_id, gender)
            ).fetchall()
            pool = sorted(pool, key=lambda a: -a["rating"])
            n = max(1, -(-len(pool) // group_size))  # ceil
            buckets = [[] for _ in range(n)]
            direction, col = 1, 0
            for a in pool:
                buckets[col].append(a)
                if direction == 1 and col == n - 1:
                    direction = -1
                elif direction == -1 and col == 0:
                    direction = 1
                else:
                    col += direction
            for i, bucket in enumerate(buckets):
                if not bucket:
                    continue
                gid = new_id()
                gname = f"Grupo {chr(65+i)}"
                conn.execute("INSERT INTO groups_t (id, category_id, gender, name) VALUES (?,?,?,?)",
                             (gid, category_id, gender, gname))
                for a in bucket:
                    conn.execute("UPDATE athletes SET group_id=? WHERE id=?", (gid, a["id"]))
                groups_created += 1

    return jsonify({"added": added, "skipped": skipped, "unmatched": unmatched,
                     "invalidCpf": invalid_cpf, "groupsCreated": groups_created})


# ---------- rodadas / pareamento ----------

@app.route("/api/groups/<gid>/rounds", methods=["GET"])
@login_required
def list_rounds(gid):
    with read_conn() as conn:
        if not can_access_group(conn, gid):
            return jsonify({"error": "Você não tem acesso a este grupo"}), 403
        matches_by_round = group_matches_by_round(conn, gid)
        return jsonify(matches_by_round)

@app.route("/api/groups/<gid>/generate-round", methods=["POST"])
@login_required
def generate_round(gid):
    data = request.get_json(silent=True) or {}
    forced_bye_id = data.get("forcedByeAthleteId")
    with write_transaction() as conn:
        if not can_access_group(conn, gid):
            return jsonify({"error": "Você não tem acesso a este grupo"}), 403
        group = conn.execute("SELECT * FROM groups_t WHERE id=?", (gid,)).fetchone()
        players = conn.execute("SELECT * FROM athletes WHERE group_id=?", (gid,)).fetchall()
        if len(players) < 2:
            return jsonify({"error": "É preciso ao menos 2 atletas no grupo"}), 400

        matches_by_round = group_matches_by_round(conn, gid)

        if group["max_rounds"] and len(matches_by_round) >= group["max_rounds"]:
            return jsonify({"error": f"Este grupo está limitado a {group['max_rounds']} rodada(s), e esse número já foi atingido."}), 400

        if matches_by_round:
            last = matches_by_round[-1]
            if any((not m["isBye"]) and m["result"] is None for m in last):
                return jsonify({"error": "Registre todos os resultados da rodada atual antes de gerar a próxima"}), 400

        all_matches_flat = flatten(matches_by_round)
        players_dicts = [dict(p) for p in players]
        score_of = {p["id"]: points_for(p["id"], all_matches_flat) for p in players_dicts}
        pool = sorted(players_dicts, key=lambda a: (-score_of[a["id"]], -a["rating"], a["full_name"]))

        bye_entries = []  # pode ter mais de um BYE na rodada quando há BYE manual + BYE automático

        # BYE manual: o organizador escolheu deliberadamente tirar um atleta específico desta rodada
        # (ex: atleta passando mal). Tem prioridade sobre a escolha automática.
        if forced_bye_id:
            idx = next((i for i, p in enumerate(pool) if p["id"] == forced_bye_id), None)
            if idx is None:
                return jsonify({"error": "Atleta escolhido para o BYE manual não está neste grupo (ou já não está mais disponível)"}), 400
            bye_entries.append(pool.pop(idx))

        # BYE automático: só entra em ação se, depois do BYE manual (se houver), ainda sobrar
        # número ímpar de jogadores para parear.
        if len(pool) % 2 == 1:
            auto_bye = None
            for i in range(len(pool) - 1, -1, -1):
                if not had_bye(pool[i]["id"], all_matches_flat):
                    auto_bye = pool.pop(i)
                    break
            if auto_bye is None:
                auto_bye = pool.pop()
            bye_entries.append(auto_bye)

        pool_for_pairing = [{"id": p["id"], "rating": p["rating"]} for p in pool]
        pairs = pair_by_score_brackets(pool_for_pairing, score_of, matches_by_round)

        round_id = new_id()
        round_number = len(matches_by_round) + 1
        conn.execute("INSERT INTO rounds (id, group_id, round_number) VALUES (?,?,?)",
                     (round_id, gid, round_number))

        for p1, p2 in pairs:
            white, black = assign_colors(p1, p2, matches_by_round)
            conn.execute("""INSERT INTO matches (id, round_id, group_id, white_id, black_id, is_bye)
                             VALUES (?,?,?,?,?,0)""",
                         (new_id(), round_id, gid, white["id"], black["id"]))
        for bye_athlete in bye_entries:
            conn.execute("""INSERT INTO matches (id, round_id, group_id, is_bye, bye_athlete_id, result, applied)
                             VALUES (?,?,?,1,?,?,1)""",
                         (new_id(), round_id, gid, bye_athlete["id"], "bye"))

        return jsonify({"ok": True, "roundNumber": round_number})


@app.route("/api/groups/<gid>/rounds/last", methods=["DELETE"])
@login_required
@admin_required
def delete_last_round(gid):
    """Desfaz a rodada mais recente de um grupo — útil quando ela foi gerada por engano
    (BYE errado, grupo errado, etc). Só permite apagar a ÚLTIMA rodada (não dá pra apagar
    uma do meio, pois quebraria a numeração e o histórico de adversários). Se algum
    resultado já tinha sido lançado nessa rodada, o rating é revertido antes de apagar."""
    with write_transaction() as conn:
        rounds = conn.execute(
            "SELECT * FROM rounds WHERE group_id=? ORDER BY round_number DESC LIMIT 1", (gid,)
        ).fetchall()
        if not rounds:
            return jsonify({"error": "Este grupo não tem nenhuma rodada para desfazer"}), 400
        last_round = rounds[0]
        matches = conn.execute("SELECT * FROM matches WHERE round_id=?", (last_round["id"],)).fetchall()
        for m in matches:
            if not m["is_bye"] and m["applied"]:
                conn.execute("UPDATE athletes SET rating = rating - ? WHERE id=?", (m["delta_white"], m["white_id"]))
                conn.execute("UPDATE athletes SET rating = rating - ? WHERE id=?", (m["delta_black"], m["black_id"]))
        conn.execute("DELETE FROM matches WHERE round_id=?", (last_round["id"],))
        conn.execute("DELETE FROM rounds WHERE id=?", (last_round["id"],))
        return jsonify({"ok": True, "removedRoundNumber": last_round["round_number"]})


@app.route("/api/matches/<mid>/result", methods=["POST"])
@login_required
def record_result(mid):
    """outcome: 'white' | 'draw' | 'black'. Protegido por transação exclusiva —
       é essa transação que resolve o problema de dois fiscais gravando ao mesmo tempo:
       o segundo pedido espera o primeiro terminar, em vez de sobrescrever."""
    data = request.get_json()
    outcome = data.get("outcome")
    if outcome not in ("white", "draw", "black"):
        return jsonify({"error": "outcome inválido"}), 400
    with write_transaction() as conn:
        m = conn.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
        if not m or m["is_bye"]:
            return jsonify({"error": "Partida não encontrada"}), 404
        if not can_access_group(conn, m["group_id"]):
            return jsonify({"error": "Você não tem acesso a este grupo"}), 403
        white = conn.execute("SELECT * FROM athletes WHERE id=?", (m["white_id"],)).fetchone()
        black = conn.execute("SELECT * FROM athletes WHERE id=?", (m["black_id"],)).fetchone()
        if not white or not black:
            return jsonify({"error": "Atletas não encontrados"}), 404

        rating_white, rating_black = white["rating"], black["rating"]
        if m["applied"]:
            rating_white -= m["delta_white"]
            rating_black -= m["delta_black"]

        delta_white, delta_black, result_str = compute_elo_update(
            rating_white, rating_black, outcome, k_for(white["k_flag"]), k_for(black["k_flag"])
        )
        new_rating_white = rating_white + delta_white
        new_rating_black = rating_black + delta_black

        conn.execute("UPDATE athletes SET rating=? WHERE id=?", (new_rating_white, white["id"]))
        conn.execute("UPDATE athletes SET rating=? WHERE id=?", (new_rating_black, black["id"]))
        conn.execute("""UPDATE matches SET result=?, delta_white=?, delta_black=?, applied=1 WHERE id=?""",
                     (result_str, delta_white, delta_black, mid))
        return jsonify({"ok": True, "result": result_str, "newRatingWhite": new_rating_white, "newRatingBlack": new_rating_black})


# ---------- classificação ----------

def compute_standings(conn, gid):
    matches_by_round = group_matches_by_round(conn, gid)
    all_matches_flat = flatten(matches_by_round)
    players = conn.execute("SELECT * FROM athletes WHERE group_id=?", (gid,)).fetchall()
    rows = []
    for p in players:
        pts = points_for(p["id"], all_matches_flat)
        opps = opponents_of(p["id"], all_matches_flat)
        buch = sum(points_for(o, all_matches_flat) for o in opps)
        w = d = l = 0
        for m in all_matches_flat:
            if m["isBye"] or m["result"] is None:
                continue
            if m["whiteId"] == p["id"]:
                if m["result"] == "1-0": w += 1
                elif m["result"] == "0.5-0.5": d += 1
                else: l += 1
            elif m["blackId"] == p["id"]:
                if m["result"] == "0-1": w += 1
                elif m["result"] == "0.5-0.5": d += 1
                else: l += 1
        per_round = []
        for rnd in matches_by_round:
            m = next((mm for mm in rnd if mm["isBye"] and mm["byeAthleteId"] == p["id"]
                      or (not mm["isBye"] and (mm["whiteId"] == p["id"] or mm["blackId"] == p["id"]))), None)
            if not m:
                per_round.append("—")
            elif m["isBye"]:
                per_round.append("BYE")
            elif m["result"] is None:
                per_round.append("···")
            elif m["whiteId"] == p["id"]:
                per_round.append("1" if m["result"] == "1-0" else ("½" if m["result"] == "0.5-0.5" else "0"))
            else:
                per_round.append("1" if m["result"] == "0-1" else ("½" if m["result"] == "0.5-0.5" else "0"))
        rows.append({
            "athleteId": p["id"], "fullName": p["full_name"], "points": pts, "buchholz": buch,
            "wins": w, "draws": d, "losses": l, "rating": p["rating"], "perRound": per_round
        })
    rows.sort(key=lambda r: (-r["points"], -r["buchholz"], -r["wins"], -r["rating"]))
    return {"maxRound": len(matches_by_round), "rows": rows}


@app.route("/api/groups/<gid>/standings", methods=["GET"])
@login_required
def standings(gid):
    with read_conn() as conn:
        if not can_access_group(conn, gid):
            return jsonify({"error": "Você não tem acesso a este grupo"}), 403
        return jsonify(compute_standings(conn, gid))


@app.route("/api/schools/standings", methods=["GET"])
@login_required
def school_standings():
    gender_filter = request.args.get("gender", "all")
    with read_conn() as conn:
        athletes = conn.execute("SELECT * FROM athletes WHERE school IS NOT NULL AND school != ''").fetchall()
        by_school = {}
        for a in athletes:
            if gender_filter != "all" and a["gender"] != gender_filter:
                continue
            matches_by_round = group_matches_by_round(conn, a["group_id"]) if a["group_id"] else []
            pts = points_for(a["id"], flatten(matches_by_round)) if a["group_id"] else 0
            s = by_school.setdefault(a["school"], {"school": a["school"], "points": 0, "athletes": 0, "m": 0, "f": 0})
            s["points"] += pts
            s["athletes"] += 1
            s["m" if a["gender"] == "M" else "f"] += 1
        rows = sorted(by_school.values(), key=lambda r: (-r["points"], -r["athletes"], r["school"]))
        return jsonify(rows)


# ---------- endpoints públicos (SEM login — usados pelo quadro público / QR Code) ----------
# Expõem só o necessário para o quadro de um grupo específico: nome, resultados e
# classificação. Nunca colégio, rating individual fora de contexto, ou dados de outros grupos.

@app.route("/api/public/groups/<gid>/rounds", methods=["GET"])
def public_rounds(gid):
    with read_conn() as conn:
        matches_by_round = group_matches_by_round(conn, gid)
        athletes = conn.execute("SELECT id, full_name FROM athletes WHERE group_id=?", (gid,)).fetchall()
        name_by_id = {a["id"]: a["full_name"] for a in athletes}
        for rnd in matches_by_round:
            for m in rnd:
                m["whiteName"] = name_by_id.get(m["whiteId"])
                m["blackName"] = name_by_id.get(m["blackId"])
                m["byeName"] = name_by_id.get(m["byeAthleteId"])
        return jsonify(matches_by_round)

@app.route("/api/public/groups/<gid>/standings", methods=["GET"])
def public_standings(gid):
    with read_conn() as conn:
        return jsonify(compute_standings(conn, gid))


# ---------- QR Code (gerado no próprio servidor, funciona sem internet externa) ----------

def public_group_url(gid):
    # request.host_url já inclui o domínio certo (localhost, ou torneioxadrez.pythonanywhere.com)
    return request.host_url.rstrip("/") + "/publico/" + gid

@app.route("/qrcode/group/<gid>.png")
def qrcode_group(gid):
    url = public_group_url(gid)
    img = qrcode.make(url, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")

@app.route("/api/public-links", methods=["GET"])
@login_required
def public_links():
    """Lista todos os grupos (que o usuário pode ver) com o link público pronto,
    para montar a tela de QR Codes no painel administrativo."""
    with read_conn() as conn:
        gids = user_group_ids(conn)
        if not gids:
            return jsonify([])
        placeholders = ",".join("?" * len(gids))
        groups = conn.execute(
            f"""SELECT g.*, c.name as category_name FROM groups_t g
                JOIN categories c ON c.id = g.category_id
                WHERE g.id IN ({placeholders})
                ORDER BY c.name, g.gender, g.name""", gids
        ).fetchall()
        out = []
        for g in groups:
            out.append({
                "id": g["id"], "name": g["name"], "gender": g["gender"],
                "categoryName": g["category_name"],
                "url": public_group_url(g["id"]),
                "qrUrl": f"/qrcode/group/{g['id']}.png",
            })
        return jsonify(out)


# ---------- histórico entre torneios (arquivar + consultar evolução de um atleta) ----------
# Cada torneio nesta base é um evento contínuo (não existe uma "data de início/fim" nativa).
# Para ter um histórico de verdade entre um torneio e outro, o organizador tira uma
# "fotografia" do estado final (nome, CPF, colégio, categoria, pontos, rating) antes de
# usar a Zona de Perigo para preparar a base para o próximo torneio. Depois, a busca por
# nome ou CPF junta essas fotografias com os dados do torneio em andamento.

@app.route("/api/tournament/archive", methods=["POST"])
@login_required
@admin_required
def archive_tournament():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    date = (data.get("date") or "").strip()
    if not name:
        return jsonify({"error": "Dê um nome para o torneio antes de arquivar"}), 400
    with write_transaction() as conn:
        athletes = conn.execute("SELECT * FROM athletes").fetchall()
        if not athletes:
            return jsonify({"error": "Não há atletas cadastrados para arquivar"}), 400
        archive_id = new_id()
        conn.execute("INSERT INTO tournament_archive (id, tournament_name, date) VALUES (?,?,?)",
                     (archive_id, name, date or None))
        for a in athletes:
            cat = conn.execute("SELECT name FROM categories WHERE id=?", (a["category_id"],)).fetchone()
            grp = conn.execute("SELECT name FROM groups_t WHERE id=?", (a["group_id"],)).fetchone() if a["group_id"] else None
            points = 0
            if a["group_id"]:
                matches_by_round = group_matches_by_round(conn, a["group_id"])
                points = points_for(a["id"], flatten(matches_by_round))
            conn.execute("""INSERT INTO archive_entries
                (id, archive_id, full_name, gender, school, category_name, group_name, k_flag, rating, points)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (new_id(), archive_id, a["full_name"], a["gender"], a["school"],
                 cat["name"] if cat else "—", grp["name"] if grp else "—", a["k_flag"], a["rating"], points))
        return jsonify({"ok": True, "archiveId": archive_id, "athletesArchived": len(athletes)})

@app.route("/api/tournament/archive", methods=["GET"])
@login_required
@admin_required
def list_archives():
    with read_conn() as conn:
        rows = conn.execute("SELECT * FROM tournament_archive ORDER BY date DESC").fetchall()
        out = []
        for r in rows:
            count = conn.execute("SELECT COUNT(*) c FROM archive_entries WHERE archive_id=?", (r["id"],)).fetchone()["c"]
            out.append({"id": r["id"], "tournamentName": r["tournament_name"], "date": r["date"], "athleteCount": count})
        return jsonify(out)

@app.route("/api/tournament/archive/<aid>", methods=["DELETE"])
@login_required
@admin_required
def delete_archive(aid):
    with write_transaction() as conn:
        conn.execute("DELETE FROM archive_entries WHERE archive_id=?", (aid,))
        conn.execute("DELETE FROM tournament_archive WHERE id=?", (aid,))
        return jsonify({"ok": True})

@app.route("/api/athletes/history", methods=["GET"])
@login_required
@admin_required
def athlete_history():
    query = (request.args.get("q") or "").strip().lower()
    if not query:
        return jsonify([])
    with read_conn() as conn:
        rows = []
        archived = conn.execute("""
            SELECT ae.*, ta.tournament_name, ta.date FROM archive_entries ae
            JOIN tournament_archive ta ON ta.id = ae.archive_id
            WHERE lower(ae.full_name) LIKE ?
        """, (f"%{query}%",)).fetchall()
        for r in archived:
            rows.append({
                "tournamentName": r["tournament_name"], "date": r["date"] or "",
                "fullName": r["full_name"], "school": r["school"], "categoryName": r["category_name"],
                "groupName": r["group_name"], "points": r["points"], "rating": r["rating"], "isCurrent": False
            })
        current = conn.execute("SELECT * FROM athletes WHERE lower(full_name) LIKE ?", (f"%{query}%",)).fetchall()
        for a in current:
            cat = conn.execute("SELECT name FROM categories WHERE id=?", (a["category_id"],)).fetchone()
            grp = conn.execute("SELECT name FROM groups_t WHERE id=?", (a["group_id"],)).fetchone() if a["group_id"] else None
            points = 0
            if a["group_id"]:
                matches_by_round = group_matches_by_round(conn, a["group_id"])
                points = points_for(a["id"], flatten(matches_by_round))
            rows.append({
                "tournamentName": "(torneio em andamento)", "date": "9999-99-99",
                "fullName": a["full_name"], "school": a["school"],
                "categoryName": cat["name"] if cat else "—", "groupName": grp["name"] if grp else "—",
                "points": points, "rating": a["rating"], "isCurrent": True
            })
        rows.sort(key=lambda r: r["date"])
        return jsonify(rows)


# ---------- zona de perigo (só admin) — para desfazer uma importação errada ----------

DANGER_CONFIRM_PHRASE = "APAGAR"

@app.route("/api/danger/delete-all-athletes", methods=["POST"])
@login_required
@admin_required
def danger_delete_all_athletes():
    """Remove TODOS os atletas — e, por consequência, todas as rodadas/partidas
    (que não fazem sentido sem os atletas). Os grupos e categorias continuam existindo,
    vazios, prontos para uma nova importação."""
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != DANGER_CONFIRM_PHRASE:
        return jsonify({"error": f'Confirmação incorreta. Digite exatamente "{DANGER_CONFIRM_PHRASE}".'}), 400
    with write_transaction() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM athletes").fetchone()["c"]
        conn.execute("DELETE FROM matches")
        conn.execute("DELETE FROM rounds")
        conn.execute("DELETE FROM athletes")
        return jsonify({"ok": True, "removedAthletes": n})

@app.route("/api/danger/delete-all-groups", methods=["POST"])
@login_required
@admin_required
def danger_delete_all_groups():
    """Remove TODOS os grupos — e suas rodadas/partidas. Os atletas continuam existindo,
    só ficam sem grupo (prontos para uma nova divisão)."""
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != DANGER_CONFIRM_PHRASE:
        return jsonify({"error": f'Confirmação incorreta. Digite exatamente "{DANGER_CONFIRM_PHRASE}".'}), 400
    with write_transaction() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM groups_t").fetchone()["c"]
        conn.execute("DELETE FROM matches")
        conn.execute("DELETE FROM rounds")
        conn.execute("UPDATE athletes SET group_id=NULL")
        conn.execute("DELETE FROM user_groups")
        conn.execute("DELETE FROM groups_t")
        return jsonify({"ok": True, "removedGroups": n})

@app.route("/api/danger/delete-everything", methods=["POST"])
@login_required
@admin_required
def danger_delete_everything():
    """Reinício total do torneio: apaga atletas, grupos, categorias, faixas de idade,
    rodadas e partidas. NÃO apaga os usuários (admin/fiscais) — essas contas continuam
    valendo para o próximo torneio."""
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != DANGER_CONFIRM_PHRASE:
        return jsonify({"error": f'Confirmação incorreta. Digite exatamente "{DANGER_CONFIRM_PHRASE}".'}), 400
    with write_transaction() as conn:
        conn.execute("DELETE FROM matches")
        conn.execute("DELETE FROM rounds")
        conn.execute("DELETE FROM user_groups")
        conn.execute("DELETE FROM athletes")
        conn.execute("DELETE FROM groups_t")
        conn.execute("DELETE FROM category_rules")
        conn.execute("DELETE FROM categories")
        return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
