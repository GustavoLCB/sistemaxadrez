"""
Motor do Sistema Suíço + Elo, portado da versão em JavaScript.
Mesma lógica já testada: pareamento por faixa de pontuação com float,
balanceamento de cor com trava de sequência, e atualização de Elo por Fator K.
"""
import uuid

K_TABLE = {"iniciante": 40, "regular": 20, "mestre": 10}

def new_id():
    return uuid.uuid4().hex

def k_for(k_flag):
    return K_TABLE.get(k_flag, 20)

def expected_score(ra, rb):
    return 1 / (1 + 10 ** ((rb - ra) / 400))


def points_for(athlete_id, matches):
    """matches: lista de dicts com whiteId/blackId/isBye/byeAthleteId/result"""
    pts = 0.0
    for m in matches:
        if m["isBye"]:
            if m["byeAthleteId"] == athlete_id:
                pts += 1
            continue
        if m["result"] is None:
            continue
        if m["whiteId"] == athlete_id:
            pts += 1 if m["result"] == "1-0" else (0.5 if m["result"] == "0.5-0.5" else 0)
        elif m["blackId"] == athlete_id:
            pts += 1 if m["result"] == "0-1" else (0.5 if m["result"] == "0.5-0.5" else 0)
    return pts


def opponents_of(athlete_id, matches):
    opps = []
    for m in matches:
        if m["isBye"]:
            continue
        if m["whiteId"] == athlete_id:
            opps.append(m["blackId"])
        elif m["blackId"] == athlete_id:
            opps.append(m["whiteId"])
    return opps


def had_bye(athlete_id, matches):
    return any(m["isBye"] and m["byeAthleteId"] == athlete_id for m in matches)


def has_played(a, b, matches):
    return b in opponents_of(a, matches)


def color_stats(athlete_id, matches_by_round):
    """matches_by_round: lista de rodadas em ordem, cada uma com sua lista de matches"""
    white = black = 0
    last_color = None
    streak = 0
    for round_matches in matches_by_round:
        m = next((mm for mm in round_matches
                  if not mm["isBye"] and (mm["whiteId"] == athlete_id or mm["blackId"] == athlete_id)), None)
        if not m:
            continue
        c = "white" if m["whiteId"] == athlete_id else "black"
        if c == "white":
            white += 1
        else:
            black += 1
        if c == last_color:
            streak += 1
        else:
            last_color = c
            streak = 1
    return {"white": white, "black": black, "lastColor": last_color, "streak": streak}


def color_conflict(p1, p2, matches_by_round):
    c1 = color_stats(p1["id"], matches_by_round)
    c2 = color_stats(p2["id"], matches_by_round)
    def must(c):
        if c["streak"] >= 2:
            return "black" if c["lastColor"] == "white" else "white"
        return None
    m1, m2 = must(c1), must(c2)
    return bool(m1 and m2 and m1 == m2)


def assign_colors(p1, p2, matches_by_round):
    c1 = color_stats(p1["id"], matches_by_round)
    c2 = color_stats(p2["id"], matches_by_round)
    def must(c):
        if c["streak"] >= 2:
            return "black" if c["lastColor"] == "white" else "white"
        return None
    must1, must2 = must(c1), must(c2)
    if must1 and must1 != must2:
        return (p1, p2) if must1 == "white" else (p2, p1)
    if must2 and must2 != must1:
        return (p2, p1) if must2 == "white" else (p1, p2)
    d1, d2 = c1["white"] - c1["black"], c2["white"] - c2["black"]
    if d1 < d2:
        return (p1, p2)
    if d2 < d1:
        return (p2, p1)
    if c1["lastColor"] == "white":
        return (p2, p1)
    if c1["lastColor"] == "black":
        return (p1, p2)
    import random
    return (p1, p2) if random.random() < 0.5 else (p2, p1)


def pair_by_score_brackets(pool, score_of, matches):
    """Regra de ouro 2: agrupa por pontuação exata, floata quando não acha par sem repetir."""
    brackets = {}
    for a in pool:
        s = score_of[a["id"]]
        brackets.setdefault(s, []).append(a)
    scores_desc = sorted(brackets.keys(), reverse=True)
    floaters = []
    pairs = []
    matches_by_round_cache = matches  # já vem organizado por rodada (lista de listas)

    def find_pair_index(p1, remaining, allow_color_conflict):
        for i, p2 in enumerate(remaining):
            if has_played(p1["id"], p2["id"], [m for rnd in matches_by_round_cache for m in rnd]):
                continue
            if not allow_color_conflict and color_conflict(p1, p2, matches_by_round_cache):
                continue
            return i
        return -1

    for score in scores_desc:
        group = sorted(floaters + brackets[score], key=lambda a: -a["rating"])
        floaters = []
        remaining = list(group)
        while len(remaining) >= 2:
            p1 = remaining.pop(0)
            idx = find_pair_index(p1, remaining, allow_color_conflict=False)
            if idx == -1:
                idx = find_pair_index(p1, remaining, allow_color_conflict=True)
            if idx == -1:
                floaters.append(p1)
                continue
            p2 = remaining.pop(idx)
            pairs.append((p1, p2))
        if len(remaining) == 1:
            floaters.append(remaining[0])

    all_matches_flat = [m for rnd in matches_by_round_cache for m in rnd]
    while len(floaters) >= 2:
        p1 = floaters.pop(0)
        idx = -1
        for i, p2 in enumerate(floaters):
            if not has_played(p1["id"], p2["id"], all_matches_flat):
                idx = i
                break
        if idx == -1:
            idx = 0
        p2 = floaters.pop(idx)
        pairs.append((p1, p2))

    return pairs


def compute_elo_update(rating_white, rating_black, outcome, k_white, k_black):
    """outcome: 'white' | 'draw' | 'black'"""
    score_white = 1 if outcome == "white" else (0.5 if outcome == "draw" else 0)
    score_black = 1 - score_white
    e_white = expected_score(rating_white, rating_black)
    e_black = 1 - e_white
    delta_white = round(k_white * (score_white - e_white))
    delta_black = round(k_black * (score_black - e_black))
    result_str = "1-0" if outcome == "white" else ("0.5-0.5" if outcome == "draw" else "0-1")
    return delta_white, delta_black, result_str
