"""
Portal do Torneio de Xadrez Escolar — Backend Flask
Versão inicial (MVP): categorias, atletas, grupos, rodadas suíças, Elo,
resultados, classificação e um endpoint público de leitura para o quadro público.
"""
from flask import Flask, request, jsonify, render_template, abort
from db import init_db, write_transaction, read_conn
from logic import (new_id, k_for, points_for, opponents_of, had_bye, has_played,
                    pair_by_score_brackets, assign_colors, compute_elo_update)
import io

app = Flask(__name__)
init_db()

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
    return render_template("index.html")

@app.route("/publico/<group_id>")
def publico(group_id):
    return render_template("publico.html", group_id=group_id)


# ---------- categorias ----------

@app.route("/api/categories", methods=["GET"])
def list_categories():
    with read_conn() as conn:
        rows = conn.execute("SELECT * FROM categories ORDER BY name").fetchall()
        return jsonify([row_to_dict(r) for r in rows])

@app.route("/api/categories", methods=["POST"])
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
def list_rules():
    with read_conn() as conn:
        rows = conn.execute("SELECT * FROM category_rules").fetchall()
        return jsonify([row_to_dict(r) for r in rows])

@app.route("/api/category-rules", methods=["POST"])
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
def delete_rule(rid):
    with write_transaction() as conn:
        conn.execute("DELETE FROM category_rules WHERE id=?", (rid,))
        return jsonify({"ok": True})


# ---------- grupos ----------

@app.route("/api/groups", methods=["GET"])
def list_groups():
    with read_conn() as conn:
        rows = conn.execute("SELECT * FROM groups_t").fetchall()
        return jsonify([row_to_dict(r) for r in rows])

@app.route("/api/groups", methods=["POST"])
def create_group():
    data = request.get_json()
    category_id, gender, name = data.get("categoryId"), data.get("gender"), (data.get("name") or "").strip()
    if not (category_id and gender in ("M", "F") and name):
        return jsonify({"error": "Dados incompletos"}), 400
    with write_transaction() as conn:
        gid = new_id()
        conn.execute("INSERT INTO groups_t (id, category_id, gender, name) VALUES (?,?,?,?)",
                     (gid, category_id, gender, name))
        return jsonify({"id": gid, "categoryId": category_id, "gender": gender, "name": name})

@app.route("/api/groups/<gid>", methods=["DELETE"])
def delete_group(gid):
    with write_transaction() as conn:
        in_use = conn.execute("SELECT COUNT(*) c FROM athletes WHERE group_id=?", (gid,)).fetchone()["c"]
        rounds_count = conn.execute("SELECT COUNT(*) c FROM rounds WHERE group_id=?", (gid,)).fetchone()["c"]
        if in_use or rounds_count:
            return jsonify({"error": "Grupo em uso por atletas ou rodadas"}), 400
        conn.execute("DELETE FROM groups_t WHERE id=?", (gid,))
        return jsonify({"ok": True})

@app.route("/api/groups/auto-split", methods=["POST"])
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
def list_athletes():
    with read_conn() as conn:
        rows = conn.execute("SELECT * FROM athletes ORDER BY full_name").fetchall()
        return jsonify([row_to_dict(r) for r in rows])

@app.route("/api/athletes", methods=["POST"])
def create_athlete():
    data = request.get_json()
    full_name = (data.get("fullName") or "").strip()
    if not full_name or not data.get("categoryId"):
        return jsonify({"error": "Nome e categoria são obrigatórios"}), 400
    with write_transaction() as conn:
        aid = data.get("id") or new_id()
        conn.execute("""INSERT INTO athletes (id, full_name, gender, category_id, group_id, k_flag, rating, age, school)
                         VALUES (?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(id) DO UPDATE SET full_name=excluded.full_name, gender=excluded.gender,
                            category_id=excluded.category_id, k_flag=excluded.k_flag, rating=excluded.rating,
                            age=excluded.age, school=excluded.school""",
                     (aid, full_name, data.get("gender", "M"), data["categoryId"], data.get("groupId"),
                      data.get("kFlag", "iniciante"), int(data.get("rating", 1500)), data.get("age"), data.get("school", "")))
        return jsonify({"id": aid})

@app.route("/api/athletes/<aid>", methods=["PATCH"])
def update_athlete(aid):
    data = request.get_json()
    fields, values = [], []
    for key, col in [("fullName", "full_name"), ("gender", "gender"), ("categoryId", "category_id"),
                      ("groupId", "group_id"), ("kFlag", "k_flag"), ("rating", "rating"),
                      ("age", "age"), ("school", "school")]:
        if key in data:
            fields.append(f"{col}=?")
            values.append(data[key])
    if not fields:
        return jsonify({"ok": True})
    values.append(aid)
    with write_transaction() as conn:
        conn.execute(f"UPDATE athletes SET {', '.join(fields)} WHERE id=?", values)
        return jsonify({"ok": True})

@app.route("/api/athletes/<aid>", methods=["DELETE"])
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
def import_athletes():
    """Recebe uma lista JSON já parseada no navegador (nome, idade, colégio, gênero opcional)
       e classifica pela idade usando as category_rules cadastradas."""
    data = request.get_json()
    rows = data.get("rows", [])
    default_gender = data.get("defaultGender", "M")
    k_flag = data.get("kFlag", "iniciante")
    default_rating = int(data.get("rating", 1500))
    group_size = max(1, int(data.get("groupSize", 25)))

    added, skipped, unmatched = 0, 0, 0
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
            dup = conn.execute(
                "SELECT COUNT(*) c FROM athletes WHERE lower(full_name)=? AND category_id=?",
                (full_name.lower(), category_id)
            ).fetchone()["c"]
            if dup:
                skipped += 1
                continue
            aid = new_id()
            conn.execute("""INSERT INTO athletes (id, full_name, gender, category_id, k_flag, rating, age, school)
                             VALUES (?,?,?,?,?,?,?,?)""",
                         (aid, full_name, gender, category_id, k_flag, default_rating, age, school))
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

    return jsonify({"added": added, "skipped": skipped, "unmatched": unmatched, "groupsCreated": groups_created})


# ---------- rodadas / pareamento ----------

@app.route("/api/groups/<gid>/rounds", methods=["GET"])
def list_rounds(gid):
    with read_conn() as conn:
        matches_by_round = group_matches_by_round(conn, gid)
        return jsonify(matches_by_round)

@app.route("/api/groups/<gid>/generate-round", methods=["POST"])
def generate_round(gid):
    with write_transaction() as conn:
        players = conn.execute("SELECT * FROM athletes WHERE group_id=?", (gid,)).fetchall()
        if len(players) < 2:
            return jsonify({"error": "É preciso ao menos 2 atletas no grupo"}), 400

        matches_by_round = group_matches_by_round(conn, gid)
        if matches_by_round:
            last = matches_by_round[-1]
            if any((not m["isBye"]) and m["result"] is None for m in last):
                return jsonify({"error": "Registre todos os resultados da rodada atual antes de gerar a próxima"}), 400

        all_matches_flat = flatten(matches_by_round)
        players_dicts = [dict(p) for p in players]
        for p in players_dicts:
            p["id"] = p["id"]
        score_of = {p["id"]: points_for(p["id"], all_matches_flat) for p in players_dicts}
        pool = sorted(players_dicts, key=lambda a: (-score_of[a["id"]], -a["rating"], a["full_name"]))

        bye_athlete = None
        if len(pool) % 2 == 1:
            for i in range(len(pool) - 1, -1, -1):
                if not had_bye(pool[i]["id"], all_matches_flat):
                    bye_athlete = pool.pop(i)
                    break
            if bye_athlete is None:
                bye_athlete = pool.pop()

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
        if bye_athlete:
            conn.execute("""INSERT INTO matches (id, round_id, group_id, is_bye, bye_athlete_id, result, applied)
                             VALUES (?,?,?,1,?,?,1)""",
                         (new_id(), round_id, gid, bye_athlete["id"], "bye"))

        return jsonify({"ok": True, "roundNumber": round_number})


@app.route("/api/matches/<mid>/result", methods=["POST"])
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

@app.route("/api/groups/<gid>/standings", methods=["GET"])
def standings(gid):
    with read_conn() as conn:
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
        return jsonify({"maxRound": len(matches_by_round), "rows": rows})


@app.route("/api/schools/standings", methods=["GET"])
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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
