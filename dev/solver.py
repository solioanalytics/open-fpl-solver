import os
import subprocess
import threading
import time
import warnings
from collections import Counter
from pathlib import Path

import highspy
import numpy as np
import pandas as pd
import requests
import sasoptpy as so

from dev.data_parser import read_data
from utils import cached_request, get_random_id

warnings.filterwarnings("ignore", category=FutureWarning, module="sasoptpy")


BINARY_THRESHOLD = 0.5  # threshold value for evaluating binary variables
BASE_URL = "https://fantasy.premierleague.com/api"
IS_COLAB = "COLAB_GPU" in os.environ
SQUAD_SIZE = 15
LINEUP_SIZE = 11
MAX_GAMEWEEK = 38
MAX_PLAYERS_PER_TEAM = 3


def generate_team_json(team_id, options):
    with requests.Session() as session:
        static_url = f"{BASE_URL}/bootstrap-static/"
        static = cached_request(static_url)
        element_to_type_dict = {x["id"]: x["element_type"] for x in static["elements"]}
        next_gw = next(x for x in static["events"] if x["is_next"])["id"]

        start_prices = {x["id"]: x["now_cost"] - x["cost_change_start"] for x in static["elements"]}
        gw1_url = f"{BASE_URL}/entry/{team_id}/event/1/picks/"
        gw1 = session.get(gw1_url).json()

        transfers_url = f"{BASE_URL}/entry/{team_id}/transfers/"
        transfers = session.get(transfers_url).json()[::-1]

        chips_url = f"{BASE_URL}/entry/{team_id}/history/"
        chips = session.get(chips_url).json()["chips"]
        fh_gws = [x["event"] for x in chips if x["name"] == "freehit"]
        wc_gws = [x["event"] for x in chips if x["name"] == "wildcard"]

    # squad will remain an ID:puchase_price map throughout iteration over transfers
    # once they have been iterated through, can then add on the current selling price
    squad = {x["element"]: start_prices[x["element"]] for x in gw1["picks"]}

    itb = 1000 - sum(squad.values())
    for t in transfers:
        if t["event"] in fh_gws:
            continue
        itb += t["element_out_cost"]
        itb -= t["element_in_cost"]
        if t["element_in"]:
            squad[t["element_in"]] = t["element_in_cost"]
        if t["element_out"]:
            del squad[t["element_out"]]

    fts = calculate_fts(transfers, next_gw, fh_gws, wc_gws)
    my_data = {"chips": chips, "picks": [], "team_id": team_id, "transfers": {"bank": itb, "limit": fts, "made": 0}}
    for player_id, purchase_price in squad.items():
        now_cost = next(x for x in static["elements"] if x["id"] == player_id)["now_cost"]

        diff = now_cost - purchase_price
        if diff > 0:
            selling_price = purchase_price + diff // 2
        else:
            selling_price = now_cost

        my_data["picks"].append(
            {"element": player_id, "purchase_price": purchase_price, "selling_price": selling_price, "element_type": element_to_type_dict[player_id]}
        )

    return my_data


def calculate_fts(transfers, next_gw, fh_gws, wc_gws):
    n_transfers = dict.fromkeys(range(2, next_gw), 0)
    for t in transfers:
        n_transfers[t["event"]] += 1
    fts = dict.fromkeys(range(2, next_gw + 1), 0)
    fts[2] = 1
    for i in range(3, next_gw + 1):
        if (i - 1) in fh_gws:
            fts[i] = fts[i - 1]
            continue
        if i - 1 in wc_gws:
            fts[i] = fts[i - 1]
            continue
        fts[i] = fts[i - 1]
        fts[i] -= n_transfers[i - 1]
        fts[i] = max(fts[i], 0)
        fts[i] += 1
        fts[i] = min(fts[i], 5)
    return fts[next_gw]


def prep_data(my_data, options):
    fpl_data = cached_request("https://fantasy.premierleague.com/api/bootstrap-static/")
    valid_ids = [x["id"] for x in fpl_data["elements"]]

    for pid, change in options.get("price_changes", []):
        if pid not in valid_ids:
            continue
        player = next(x for x in fpl_data["elements"] if x["id"] == pid)
        player["now_cost"] += change

    if options.get("override_next_gw", None):
        gw = int(options["override_next_gw"])
    else:
        gw = 0
        for e in fpl_data["events"]:
            if e["is_next"]:
                gw = e["id"]
                break

    horizon = options.get("horizon", 3)

    element_data = pd.DataFrame(fpl_data["elements"])
    team_data = pd.DataFrame(fpl_data["teams"])
    elements_team = pd.merge(element_data, team_data, left_on="team", right_on="id")

    element_to_team = {x["id"]: x["team"] for x in fpl_data["elements"]}  # dict mapping element to team id
    max_players_from_team = Counter([element_to_team[x["element"]] for x in my_data["picks"]]).most_common(1)[0][1] if my_data["picks"] else 3
    data = read_data(options)

    merged_data = pd.merge(elements_team, data, left_on="id_x", right_on="ID")
    merged_data.set_index(["id_x"], inplace=True)

    # Drop duplicates
    merged_data = merged_data.drop_duplicates(subset=["ID"], keep="first")

    # Check if data exists
    for week in range(gw, min(39, gw + horizon)):
        if f"{week}_Pts" not in data.keys():
            raise ValueError(f"{week}_Pts is not inside prediction data - change your horizon or update your prediction data")

    original_keys = merged_data.columns.to_list()
    keys = [k for k in original_keys if "_Pts" in k]
    min_keys = [k for k in original_keys if "_xMins" in k]
    merged_data["total_ev"] = merged_data[keys].sum(axis=1)
    merged_data["total_min"] = merged_data[min_keys].sum(axis=1)

    merged_data.sort_values(by=["total_ev"], ascending=[False], inplace=True)

    locked_next_gw = [int(i[0]) if isinstance(i, list) else int(i) for i in options.get("locked_next_gw", [])]
    safe_players_due_price = []
    for pos, vals in options.get("pick_prices", {}).items():
        if vals is None or vals == "":
            continue
        price_vals = [float(i) for i in vals.split(",")]
        pp = merged_data[(merged_data["Pos"] == pos) & ((merged_data["now_cost"] / 10).isin(price_vals))]["ID"].to_list()
        safe_players_due_price += pp

    # Filter players by total EV
    cutoff = merged_data["total_ev"].quantile((100 - options.get("keep_top_ev_percent", 10)) / 100)
    safe_players_due_ev = merged_data[(merged_data["total_ev"] > cutoff)]["ID"].tolist()

    initial_squad = [int(i["element"]) for i in my_data["picks"]]
    safe_players = initial_squad + options.get("locked", []) + options.get("keep", []) + locked_next_gw + safe_players_due_price + safe_players_due_ev

    for bt in options.get("booked_transfers", []):
        if bt.get("transfer_in"):
            safe_players.append(bt["transfer_in"])
        if bt.get("transfer_out"):
            safe_players.append(bt["transfer_out"])

    # Filter players by xMin
    xmin_lb = options.get("xmin_lb", 100)
    num_players_before = len(merged_data)
    merged_data = merged_data[(merged_data["total_min"] >= xmin_lb) | (merged_data["ID"].isin(safe_players))].copy()

    # Filter by ev per price
    ev_per_price_cutoff = options.get("ev_per_price_cutoff", 0)
    if ev_per_price_cutoff != 0:
        cutoff = (merged_data["total_ev"] / merged_data["now_cost"]).quantile(ev_per_price_cutoff / 100)
        merged_data = merged_data[(merged_data["total_ev"] / merged_data["now_cost"] > cutoff) | (merged_data["ID"].isin(safe_players))].copy()

    num_players_after = len(merged_data)
    print(f"Filtered player pool from {num_players_before} to {num_players_after} players")

    if options.get("randomized", False):
        rng = np.random.default_rng(seed=options.get("randomization_seed"))
        gws = list(range(gw, min(39, gw + horizon)))
        for w in gws:
            noise = merged_data[f"{w}_Pts"] * (92 - merged_data[f"{w}_xMins"]) / 134 * rng.standard_normal(size=len(merged_data))
            merged_data[f"{w}_Pts"] = merged_data[f"{w}_Pts"] + noise * options.get("randomization_strength", 1)

    type_data = pd.DataFrame(fpl_data["element_types"]).set_index(["id"])

    buy_price = (merged_data["now_cost"] / 10).to_dict()
    sell_price = {i["element"]: i["selling_price"] / 10 for i in my_data["picks"]}
    price_modified_players = []

    preseason = options.get("preseason", False)
    if not preseason:
        for i in my_data["picks"]:
            if buy_price[i["element"]] != sell_price[i["element"]]:
                price_modified_players.append(i["element"])
                print(f"Added player {i['element']} to list, buy price {buy_price[i['element']]} sell price {sell_price[i['element']]}")

    itb = my_data["transfers"]["bank"] / 10
    ft_base = None
    if my_data["transfers"]["limit"] is None:
        ft = 1
        ft_base = 1
    else:
        ft = my_data["transfers"]["limit"] - my_data["transfers"]["made"]
        ft_base = my_data["transfers"]["limit"]

    ft = max(ft, 0)

    # If wildcard is active, then you have: "status_for_entry": "active" under my_data['chips']
    # can only pass the check when using "team_data": "json"
    for c in my_data["chips"]:
        if c["name"] == "wildcard" and c.get("status_for_entry", "") == "active":
            options["use_wc"] = [gw]
            if options["chip_limits"]["wc"] == 0:
                options["chip_limits"]["wc"] = 1
            break

    # Fixture info
    team_code_dict = team_data.set_index("id")["name"].to_dict()
    fixture_data = cached_request("https://fantasy.premierleague.com/api/fixtures/")
    fixtures = [{"gw": f["event"], "home": team_code_dict[f["team_h"]], "away": team_code_dict[f["team_a"]]} for f in fixture_data]

    return {
        "merged_data": merged_data,
        "team_data": team_data,
        "my_data": my_data,
        "type_data": type_data,
        "next_gw": gw,
        "initial_squad": initial_squad,
        "sell_price": sell_price,
        "buy_price": buy_price,
        "price_modified_players": price_modified_players,
        "itb": itb,
        "ft": ft,
        "ft_base": ft_base,
        "fixtures": fixtures,
        "max_players_from_team": max_players_from_team,
    }


def solve_multi_period_fpl(data, options):
    """
    Solves multi-objective FPL problem with transfers

    Parameters
    ----------
    data: dict
        Pre-processed data for the problem definition
    options: dict
        User controlled values for the problem instance
    """

    print(
        "This solver is free for personal, educational, or non-commercial use under the "
        "Apache License 2.0. Commercial entities must obtain a Commercial License before "
        "accessing, viewing, or using the code for any commercial purposes. Unauthorized "
        "access or use by commercial entities without a valid commercial license is strictly "
        "prohibited. To obtain a commercial license, please contact us at "
        "info@fploptimized.com."
    )

    try:
        commit_hash = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL).decode("utf-8").strip()
        commit_count = subprocess.check_output(["git", "rev-list", "--count", "HEAD"], stderr=subprocess.DEVNULL).decode("utf-8").strip()
        version = f"{commit_count} - {commit_hash}"
        print(f"Version: {version}")
    except Exception:
        pass

    # Arguments
    problem_id = get_random_id(5)
    horizon = options.get("horizon", 3)
    objective = options.get("objective", "decay")
    decay_base = options.get("decay_base", 0.84)
    bench_weights = options.get("bench_weights", {0: 0.03, 1: 0.21, 2: 0.06, 3: 0.002})
    bench_weights = {int(key): value for (key, value) in bench_weights.items()}
    # wc_limit = options.get('wc_limit', 0)
    ft_value = options.get("ft_value", 1.5)
    ft_value_list = options.get("ft_value_list", {})
    # ft_gw_value = {}
    ft_use_penalty = options.get("ft_use_penalty", None)
    itb_value = options.get("itb_value", 0.08)
    initial_ft = max(0, data.get("ft", 1))
    ft_base = data.get("ft_base", 1)
    chip_limits = options.get("chip_limits", {})
    allowed_chip_gws = options.get("allowed_chip_gws", {})
    forced_chip_gws = options.get("forced_chip_gws", {})
    booked_transfers = options.get("booked_transfers", [])
    preseason = options.get("preseason", False)
    itb_loss_per_transfer = options.get("itb_loss_per_transfer", None)
    if itb_loss_per_transfer is None:
        itb_loss_per_transfer = 0
    weekly_hit_limit = options.get("weekly_hit_limit", None)

    # Data
    problem_name = f"mp_h{horizon}_regular" if objective == "regular" else f"mp_h{horizon}_o{objective[0]}_d{decay_base}"
    merged_data = data["merged_data"]
    team_data = data["team_data"]
    type_data = data["type_data"]
    next_gw = data["next_gw"]
    initial_squad = data["initial_squad"]
    itb = data["itb"]
    fixtures = data["fixtures"]
    if preseason:
        itb = 100
        threshold_gw = 2
    else:
        threshold_gw = next_gw

    # Sets
    players = merged_data.index.to_list()
    el_types = type_data.index.to_list()
    teams = team_data["name"].to_list()
    last_gw = next_gw + horizon - 1
    if last_gw > MAX_GAMEWEEK:
        last_gw = MAX_GAMEWEEK
        horizon = MAX_GAMEWEEK + 1 - next_gw
    gws = list(range(next_gw, last_gw + 1))
    all_gw = [next_gw - 1, *gws]
    order = [0, 1, 2, 3]
    price_modified_players = data["price_modified_players"]
    ft_states = [0, 1, 2, 3, 4, 5]

    # Model
    model = so.Model(name=problem_name)

    # Variables
    squad = model.add_variables(players, all_gw, name="squad", vartype=so.binary)
    squad_fh = model.add_variables(players, gws, name="squad_fh", vartype=so.binary)
    lineup = model.add_variables(players, gws, name="lineup", vartype=so.binary)
    captain = model.add_variables(players, gws, name="captain", vartype=so.binary)
    vicecap = model.add_variables(players, gws, name="vicecap", vartype=so.binary)
    bench = model.add_variables(players, gws, order, name="bench", vartype=so.binary)
    transfer_in = model.add_variables(players, gws, name="transfer_in", vartype=so.binary)
    transfer_out_first = model.add_variables(price_modified_players, gws, name="tr_out_first", vartype=so.binary)
    transfer_out_regular = model.add_variables(players, gws, name="tr_out_reg", vartype=so.binary)
    transfer_out = {
        (p, w): transfer_out_regular[p, w] + (transfer_out_first[p, w] if p in price_modified_players else 0) for p in players for w in gws
    }
    in_the_bank = model.add_variables(all_gw, name="itb", vartype=so.continuous, lb=0)
    fts = model.add_variables(all_gw, name="ft", vartype=so.integer, lb=0, ub=5)
    ft_above_ub = model.add_variables(gws, name="ft_above", vartype=so.binary)
    ft_below_lb = model.add_variables(gws, name="ft_below", vartype=so.binary)
    fts_state = model.add_variables(gws, ft_states, name="ft_state", vartype=so.binary)
    penalized_transfers = model.add_variables(gws, name="pt", vartype=so.integer, lb=0)
    aux = model.add_variables(gws, name="aux", vartype=so.binary)
    transfer_count = model.add_variables(gws, name="trc", vartype=so.integer, lb=0, ub=SQUAD_SIZE)

    use_wc = model.add_variables(gws, name="use_wc", vartype=so.binary)
    use_bb = model.add_variables(gws, name="use_bb", vartype=so.binary)
    use_fh = model.add_variables(gws, name="use_fh", vartype=so.binary)
    use_tc = model.add_variables(players, gws, name="use_tc", vartype=so.binary)

    # Dictionaries
    lineup_type_count = {(t, w): so.expr_sum(lineup[p, w] for p in players if merged_data.loc[p, "element_type"] == t) for t in el_types for w in gws}
    squad_type_count = {(t, w): so.expr_sum(squad[p, w] for p in players if merged_data.loc[p, "element_type"] == t) for t in el_types for w in gws}
    squad_fh_type_count = {
        (t, w): so.expr_sum(squad_fh[p, w] for p in players if merged_data.loc[p, "element_type"] == t) for t in el_types for w in gws
    }
    player_type = merged_data["element_type"].to_dict()
    # player_price = (merged_data['now_cost'] / 10).to_dict()
    sell_price = data["sell_price"]
    buy_price = data["buy_price"]
    sold_amount = {
        w: (
            so.expr_sum(sell_price[p] * transfer_out_first[p, w] for p in price_modified_players)
            + so.expr_sum(buy_price[p] * transfer_out_regular[p, w] for p in players)
        )
        for w in gws
    }
    fh_sell_price = {p: sell_price[p] if p in price_modified_players else buy_price[p] for p in players}
    bought_amount = {w: so.expr_sum(buy_price[p] * transfer_in[p, w] for p in players) for w in gws}
    points_player_week = {(p, w): merged_data.loc[p, f"{w}_Pts"] for p in players for w in gws}
    minutes_player_week = {(p, w): merged_data.loc[p, f"{w}_xMins"] for p in players for w in gws}
    player_team = {p: merged_data.loc[p, "name"] for p in players}
    squad_count = {w: so.expr_sum(squad[p, w] for p in players) for w in gws}
    squad_fh_count = {w: so.expr_sum(squad_fh[p, w] for p in players) for w in gws}
    num_transfers = {w: so.expr_sum(transfer_out[p, w] for p in players) for w in gws}
    transfer_diff = {w: num_transfers[w] - fts[w] - SQUAD_SIZE * use_wc[w] for w in gws}
    use_tc_gw = {w: so.expr_sum(use_tc[p, w] for p in players) for w in gws}

    # Initial conditions
    model.add_constraints((squad[p, next_gw - 1] == 1 for p in initial_squad), name="initial_squad_players")
    model.add_constraints((squad[p, next_gw - 1] == 0 for p in players if p not in initial_squad), name="initial_squad_others")
    model.add_constraint(in_the_bank[next_gw - 1] == itb, name="initial_itb")
    model.add_constraint(fts[next_gw] == initial_ft * (1 - use_wc[next_gw]) + ft_base * use_wc[next_gw], name="initial_ft")
    model.add_constraints((fts[w] >= 1 for w in gws if w > next_gw), name="future_ft_limit")

    # Constraints
    model.add_constraints((squad_count[w] == SQUAD_SIZE for w in gws), name="squad_count")
    model.add_constraints((squad_fh_count[w] == SQUAD_SIZE * use_fh[w] for w in gws), name="squad_fh_count")
    model.add_constraints(
        (so.expr_sum(lineup[p, w] for p in players) == LINEUP_SIZE + (SQUAD_SIZE - LINEUP_SIZE) * use_bb[w] for w in gws), name="lineup_count"
    )
    model.add_constraints((so.expr_sum(bench[p, w, 0] for p in players if player_type[p] == 1) == 1 - use_bb[w] for w in gws), name="bench_gk")
    model.add_constraints((so.expr_sum(bench[p, w, o] for p in players) == 1 - use_bb[w] for w in gws for o in [1, 2, 3]), name="bench_count")
    model.add_constraints((so.expr_sum(captain[p, w] for p in players) == 1 for w in gws), name="captain_count")
    model.add_constraints((so.expr_sum(vicecap[p, w] for p in players) == 1 for w in gws), name="vicecap_count")
    model.add_constraints((lineup[p, w] <= squad[p, w] + use_fh[w] for p in players for w in gws), name="lineup_squad_rel")
    model.add_constraints((bench[p, w, o] <= squad[p, w] + use_fh[w] for p in players for w in gws for o in order), name="bench_squad_rel")
    model.add_constraints((lineup[p, w] <= squad_fh[p, w] + 1 - use_fh[w] for p in players for w in gws), name="lineup_squad_fh_rel")
    model.add_constraints((bench[p, w, o] <= squad_fh[p, w] + 1 - use_fh[w] for p in players for w in gws for o in order), name="bench_squad_fh_rel")
    model.add_constraints((captain[p, w] <= lineup[p, w] for p in players for w in gws), name="captain_lineup_rel")
    model.add_constraints((vicecap[p, w] <= lineup[p, w] for p in players for w in gws), name="vicecap_lineup_rel")
    model.add_constraints((captain[p, w] + vicecap[p, w] <= 1 for p in players for w in gws), name="cap_vc_rel")
    model.add_constraints((lineup[p, w] + so.expr_sum(bench[p, w, o] for o in order) <= 1 for p in players for w in gws), name="lineup_bench_rel")
    model.add_constraints((lineup_type_count[t, w] >= type_data.loc[t, "squad_min_play"] for t in el_types for w in gws), name="valid_formation_lb")
    model.add_constraints(
        (lineup_type_count[t, w] <= type_data.loc[t, "squad_max_play"] + use_bb[w] for t in el_types for w in gws), name="valid_formation_ub"
    )
    model.add_constraints((squad_type_count[t, w] == type_data.loc[t, "squad_select"] for t in el_types for w in gws), name="valid_squad")
    model.add_constraints(
        (squad_fh_type_count[t, w] == type_data.loc[t, "squad_select"] * use_fh[w] for t in el_types for w in gws), name="valid_squad_fh"
    )

    # special case where user's current squad has too many players from the same team
    # only works for 4 players from same team at the moment
    if data["max_players_from_team"] > MAX_PLAYERS_PER_TEAM:
        no_transfer = model.add_variables(gws, vartype=so.binary, name="no_transfer")
        model.add_constraints((transfer_count[w] <= SQUAD_SIZE * (1 - no_transfer[w]) for w in gws), name="no_transfer_1")
        model.add_constraints((transfer_count[w] >= 1 - SQUAD_SIZE * no_transfer[w] for w in gws), name="no_transfer_2")

        model.add_constraints(
            (so.expr_sum(squad[p, w] for p in players if player_team[p] == t) <= MAX_PLAYERS_PER_TEAM + no_transfer[w] for t in teams for w in gws),
            name="team_limit",
        )

    else:  # normal case where user has a valid squad
        model.add_constraints(
            (so.expr_sum(squad[p, w] for p in players if player_team[p] == t) <= MAX_PLAYERS_PER_TEAM for t in teams for w in all_gw),
            name="team_limit",
        )

    model.add_constraints(
        (so.expr_sum(squad_fh[p, w] for p in players if player_team[p] == t) <= MAX_PLAYERS_PER_TEAM * use_fh[w] for t in teams for w in gws),
        name="team_limit_fh",
    )
    ## Transfer constraints
    model.add_constraints(
        (squad[p, w] == squad[p, w - 1] + transfer_in[p, w] - transfer_out[p, w] for p in players for w in gws), name="squad_transfer_rel"
    )
    model.add_constraints(
        (
            in_the_bank[w]
            == in_the_bank[w - 1] + sold_amount[w] - bought_amount[w] - (transfer_count[w] * itb_loss_per_transfer if w > next_gw else 0)
            for w in gws
        ),
        name="cont_budget",
    )
    model.add_constraints(
        (
            so.expr_sum(fh_sell_price[p] * squad[p, w - 1] for p in players) + in_the_bank[w - 1]
            >= so.expr_sum(fh_sell_price[p] * squad_fh[p, w] for p in players)
            for w in gws
        ),
        name="fh_budget",
    )
    model.add_constraints((transfer_in[p, w] <= 1 - use_fh[w] for p in players for w in gws), name="no_tr_in_fh")
    model.add_constraints((transfer_out[p, w] <= 1 - use_fh[w] for p in players for w in gws), name="no_tr_out_fh")

    ## Free transfer constraints
    # 2024-2025 variation: min 1 / max 5 / roll over WC & FH
    # raw_gw_ft = {w: fts[w] - transfer_count[w] + 1 - use_wc[w] - use_fh[w] for w in gws}

    # 2056-26 afcon variation: always have 5 ft in gw16 no matter what
    afcon_gw = 15
    raw_gw_ft = {w: fts[w] - transfer_count[w] + (5 if w == afcon_gw else 1) - use_wc[w] - use_fh[w] for w in gws}
    m = 20  # big m for bounding constraints, picked 20 because nobody will ever get to 20 ft in a solve

    # FT_BELOW_LB AND FT_ABOVE_UB LOGIC

    # ft_above_ub[w] == 1  <=>  raw_gw_ft[w] > 5
    model.add_constraints((raw_gw_ft[w] >= 6 - m * (1 - ft_above_ub[w]) for w in gws), name="ft_above_ub_lb")
    model.add_constraints((raw_gw_ft[w] <= 5 + m * ft_above_ub[w] for w in gws), name="ft_above_ub_ub")

    # ft_below_lb[w] == 1  <=>  raw_gw_ft[w] < 0
    model.add_constraints((raw_gw_ft[w] <= -1 + m * (1 - ft_below_lb[w]) for w in gws), name="ft_below_lb_ub")
    model.add_constraints((raw_gw_ft[w] >= 0 - m * ft_below_lb[w] for w in gws), name="ft_below_lb_lb")

    # FREE TRANSFER LOGIC

    # raw_gw_ft[w] > 5 => fts[w+1] = 5
    model.add_constraints((fts[w + 1] <= 5 + m * (1 - ft_above_ub[w]) for w in gws if w + 1 in gws), name="ft_cap_upper_ub")
    model.add_constraints((fts[w + 1] >= 5 - m * (1 - ft_above_ub[w]) for w in gws if w + 1 in gws), name="ft_cap_upper_lb")

    # raw_gw_ft[w] < 0 => fts[w+1] = 1
    model.add_constraints((fts[w + 1] <= 1 + m * (1 - ft_below_lb[w]) for w in gws if w + 1 in gws), name="ft_cap_lower_ub")
    model.add_constraints((fts[w + 1] >= 1 - m * (1 - ft_below_lb[w]) for w in gws if w + 1 in gws), name="ft_cap_lower_lb")

    # 0 <= raw_gw_ft <= 5 => fts[w+1] = raw_gw_ft[w]
    model.add_constraints((fts[w + 1] - raw_gw_ft[w] <= m * (ft_above_ub[w] + ft_below_lb[w]) for w in gws if w + 1 in gws), name="ft_inrange_ub")
    model.add_constraints((raw_gw_ft[w] - fts[w + 1] <= m * (ft_above_ub[w] + ft_below_lb[w]) for w in gws if w + 1 in gws), name="ft_inrange_lb")

    model.add_constraints((fts[w] == so.expr_sum(fts_state[w, s] * s for s in ft_states) for w in gws), name="ftsc1")
    model.add_constraints((so.expr_sum(fts_state[w, s] for s in ft_states) == 1 for w in gws), name="ftsc2")

    if preseason and threshold_gw in gws:
        model.add_constraint(fts[threshold_gw] == 1, name="ps_initial_ft")
    model.add_constraints((penalized_transfers[w] >= transfer_diff[w] for w in gws), name="pen_transfer_rel")

    ## Chip constraints
    model.add_constraints((use_wc[w] + use_fh[w] + use_bb[w] + use_tc_gw[w] <= 1 for w in gws), name="single_chip")
    model.add_constraints((aux[w] <= 1 - use_wc[w - 1] for w in gws if w > next_gw), name="ft_after_wc")
    model.add_constraints((aux[w] <= 1 - use_fh[w - 1] for w in gws if w > next_gw), name="ft_after_fh")
    model.add_constraints((use_tc[p, w] <= captain[p, w] for p in players for w in gws), name="tc_cap_rel")

    wc = options.get("use_wc", [])
    if len(wc) > 0:
        model.add_constraints((use_wc[w] == 1 for w in wc), name="force_wc")
        chip_limits["wc"] = len(wc)

    bb = options.get("use_bb", [])
    if len(bb) > 0:
        model.add_constraints((use_bb[w] == 1 for w in bb), name="force_bb")
        chip_limits["bb"] = len(bb)

    fh = options.get("use_fh", [])
    if len(fh) > 0:
        model.add_constraints((use_fh[w] == 1 for w in fh), name="force_fh")
        chip_limits["fh"] = len(fh)

    tc = options.get("use_tc", [])
    if len(tc) > 0:
        model.add_constraints((use_tc_gw[w] == 1 for w in tc), name="force_tc")
        chip_limits["tc"] = len(tc)

    if len(allowed_chip_gws.get("wc", [])) > 0:
        gws_banned = [w for w in gws if w not in allowed_chip_gws["wc"]]
        model.add_constraints((use_wc[w] == 0 for w in gws_banned), name="banned_wc_gws")
        chip_limits["wc"] = 1
    if len(allowed_chip_gws.get("fh", [])) > 0:
        gws_banned = [w for w in gws if w not in allowed_chip_gws["fh"]]
        model.add_constraints((use_fh[w] == 0 for w in gws_banned), name="banned_fh_gws")
        chip_limits["fh"] = 1
    if len(allowed_chip_gws.get("bb", [])) > 0:
        gws_banned = [w for w in gws if w not in allowed_chip_gws["bb"]]
        model.add_constraints((use_bb[w] == 0 for w in gws_banned), name="banned_bb_gws")
        chip_limits["bb"] = 1
    if len(allowed_chip_gws.get("tc", [])) > 0:
        gws_banned = [w for w in gws if w not in allowed_chip_gws["tc"]]
        model.add_constraints((use_tc_gw[w] == 0 for w in gws_banned), name="banned_tc_gws")
        chip_limits["tc"] = 1

    if len(forced_chip_gws.get("wc", [])) > 0:
        model.add_constraint(so.expr_sum(use_wc[w] for w in forced_chip_gws["wc"]) == 1, name="force_wc_gw")
        chip_limits["wc"] = 1
    if len(forced_chip_gws.get("fh", [])) > 0:
        model.add_constraint(so.expr_sum(use_fh[w] for w in forced_chip_gws["fh"]) == 1, name="force_fh_gw")
        chip_limits["fh"] = 1
    if len(forced_chip_gws.get("bb", [])) > 0:
        model.add_constraint(so.expr_sum(use_bb[w] for w in forced_chip_gws["bb"]) == 1, name="force_bb_gw")
        chip_limits["bb"] = 1
    if len(forced_chip_gws.get("tc", [])) > 0:
        model.add_constraint(so.expr_sum(use_tc_gw[w] for w in forced_chip_gws["tc"]) == 1, name="force_tc_gw")
        chip_limits["tc"] = 1

    model.add_constraint(so.expr_sum(use_wc[w] for w in gws) <= chip_limits.get("wc", 0), name="use_wc_limit")
    model.add_constraint(so.expr_sum(use_bb[w] for w in gws) <= chip_limits.get("bb", 0), name="use_bb_limit")
    model.add_constraint(so.expr_sum(use_fh[w] for w in gws) <= chip_limits.get("fh", 0), name="use_fh_limit")
    model.add_constraint(so.expr_sum(use_tc_gw[w] for w in gws) <= chip_limits.get("tc", 0), name="use_tc_limit")
    model.add_constraints((squad_fh[p, w] <= use_fh[w] for p in players for w in gws), name="fh_squad_logic")

    ## Multiple-sell fix
    model.add_constraints(
        (transfer_out_first[p, w] + transfer_out_regular[p, w] <= 1 for p in price_modified_players for w in gws), name="multi_sell_1"
    )
    model.add_constraints(
        (
            horizon * so.expr_sum(transfer_out_first[p, w] for w in gws if w <= wbar)
            >= so.expr_sum(transfer_out_regular[p, w] for w in gws if w >= wbar)
            for p in price_modified_players
            for wbar in gws
        ),
        name="multi_sell_2",
    )
    model.add_constraints((so.expr_sum(transfer_out_first[p, w] for w in gws) <= 1 for p in price_modified_players), name="multi_sell_3")

    ## Transfer in/out fix
    model.add_constraints((transfer_in[p, w] + transfer_out[p, w] <= 1 for p in players for w in gws), name="tr_in_out_limit")

    ## Tr Count Constraints
    ft_penalty = dict.fromkeys(gws, 0)
    model.add_constraints((transfer_count[w] >= num_transfers[w] - SQUAD_SIZE * use_wc[w] for w in gws), name="trc_lb")
    model.add_constraints((transfer_count[w] <= num_transfers[w] for w in gws), name="trc_ub1")
    model.add_constraints((transfer_count[w] <= SQUAD_SIZE * (1 - use_wc[w]) for w in gws), name="trc_ub2")
    if ft_use_penalty is not None:
        ft_penalty = {w: ft_use_penalty * transfer_count[w] for w in gws}

    ## Optional constraints
    if options.get("banned", None):
        print("OC - Banned")
        banned_players = options["banned"]
        model.add_constraints((so.expr_sum(squad[p, w] for w in gws) == 0 for p in banned_players if p in players), name="ban_player")
        model.add_constraints((so.expr_sum(squad_fh[p, w] for w in gws) == 0 for p in banned_players if p in players), name="ban_player_fh")

    if options.get("banned_next_gw", None):
        print("OC - Banned Next GW")
        banned_in_gw = [(x, gws[0]) if isinstance(x, int) else tuple(x) for x in options["banned_next_gw"]]
        model.add_constraints((squad[p0, p1] == 0 for (p0, p1) in banned_in_gw if p0 in players), name="ban_player_specified_gw")
        model.add_constraints((squad_fh[p0, p1] == 0 for (p0, p1) in banned_in_gw if p0 in players), name="ban_player_specified_gw_fh")

    if options.get("locked", None):
        print("OC - Locked")
        locked_players = options["locked"]
        model.add_constraints((squad[p, w] + squad_fh[p, w] == 1 for p in locked_players for w in gws), name="lock_player")

    if options.get("locked_next_gw", None):
        print("OC - Locked Next GW")
        locked_in_gw = [(x, gws[0]) if isinstance(x, int) else tuple(x) for x in options["locked_next_gw"]]
        model.add_constraints((squad[p0, p1] == 1 for (p0, p1) in locked_in_gw), name="lock_player_specified_gw")

    if options.get("no_future_transfer", None):
        print("OC - No Future Tr")
        model.add_constraint(
            so.expr_sum(transfer_in[p, w] for p in players for w in gws if w > next_gw and w != options.get("use_wc")) == 0, name="no_future_transfer"
        )

    if options.get("no_transfer_last_gws", None):
        print("OC - No TR last GWs")
        no_tr_gws = options["no_transfer_last_gws"]
        if horizon > no_tr_gws:
            model.add_constraints(
                (so.expr_sum(transfer_in[p, w] for p in players) <= SQUAD_SIZE * use_wc[w] for w in gws if w > last_gw - no_tr_gws), name="tr_ban_gws"
            )

    if options.get("num_transfers", None) is not None:
        print("OC - Num Transfers")
        model.add_constraint(so.expr_sum(transfer_in[p, next_gw] for p in players) == options["num_transfers"], name="tr_limit")

    if options.get("hit_limit", None):
        print("OC - Hit Limit")
        model.add_constraint(so.expr_sum(penalized_transfers[w] for w in gws) <= int(options["hit_limit"]), name="horizon_hit_limit")

    if options.get("weekly_hit_limit") is not None:
        weekly_hit_limit = int(options.get("weekly_hit_limit"))
        model.add_constraints((penalized_transfers[w] <= weekly_hit_limit for w in gws), name="gw_hit_lim")

    # if options.get("ft_custom_value", None) is not None:
    #     ft_custom_value = {int(key): value for (key, value) in options.get('ft_custom_value', {}).items()}
    #     ft_gw_value = {**{gw: ft_value for gw in gws}, **ft_custom_value}

    if options.get("future_transfer_limit", None):
        print("OC - Future TR Limit")
        model.add_constraint(
            so.expr_sum(transfer_in[p, w] for p in players for w in gws if w > next_gw and w not in options.get("use_wc", []))
            <= options["future_transfer_limit"],
            name="future_tr_limit",
        )

    if options.get("no_transfer_gws", None):
        print("OC - No TR GWs")
        if len(options["no_transfer_gws"]) > 0:
            model.add_constraint(so.expr_sum(transfer_in[p, w] for p in players for w in options["no_transfer_gws"]) == 0, name="banned_gws_for_tr")

    if options.get("no_transfer_by_position", None):
        print("OC - No TR by position")
        if len(options["no_transfer_by_position"]) > 0:
            # ignore w=1 as you must transfer in a full squad
            model.add_constraints(
                (
                    transfer_in[p, w] <= use_wc[w]
                    for p in players
                    for w in gws
                    if w > 1
                    if merged_data.loc[p, "Pos"] in options["no_transfer_by_position"]
                ),
                name="no_tr_by_pos",
            )

    max_defs_per_team = options.get("max_defenders_per_team", 3)
    if max_defs_per_team < MAX_PLAYERS_PER_TEAM:  # only add constraints if necessary
        model.add_constraints(
            (
                so.expr_sum(squad[p, w] for p in players if player_team[p] == t and merged_data.loc[p, "Pos"] in {"G", "D"}) <= max_defs_per_team
                for t in teams
                for w in gws
            ),
            name="defenders_per_team_limit",
        )
        model.add_constraints(
            (
                so.expr_sum(squad_fh[p, w] for p in players if player_team[p] == t and merged_data.loc[p, "Pos"] in {"G", "D"})
                <= max_defs_per_team * use_fh[w]
                for t in teams
                for w in gws
            ),
            name="defenders_per_team_limit_fh",
        )

    for booked_transfer in booked_transfers:
        print("OC - Booked TRs")
        transfer_gw = booked_transfer.get("gw", None)

        if transfer_gw is None:
            continue

        player_in = booked_transfer.get("transfer_in", None)
        player_out = booked_transfer.get("transfer_out", None)

        if player_in is not None:
            model.add_constraint(transfer_in[player_in, transfer_gw] == 1, name=f"booked_transfer_in_{transfer_gw}_{player_in}")
        if player_out is not None:
            model.add_constraint(transfer_out[player_out, transfer_gw] == 1, name=f"booked_transfer_out_{transfer_gw}_{player_out}")

    cp_penalty = {}
    if options.get("no_opposing_play") is True:
        print("OC - No Opposing Play")
        gw_opp_teams = {
            w: [(f["home"], f["away"]) for f in fixtures if f["gw"] == w] + [(f["away"], f["home"]) for f in fixtures if f["gw"] == w] for w in gws
        }
        for gw in gws:
            [i for i in fixtures if i["gw"] == gw]
            if options.get("opposing_play_group", "all") == "all":
                opposing_players = [(p1, p2) for p1 in players for p2 in players if (player_team[p1], player_team[p2]) in gw_opp_teams[gw]]
                model.add_constraints((lineup[p1, gw] + lineup[p2, gw] <= 1 for (p1, p2) in opposing_players), name=f"no_opp_{gw}")
            elif options.get("opposing_play_group") == "position":
                opposing_positions = [
                    (1, 3),
                    (1, 4),
                    (2, 3),
                    (2, 4),
                    (3, 1),
                    (4, 1),
                    (3, 2),
                    (4, 2),
                ]  # gk vs mid, gk vs fwd, def vs mid, def vs fwd
                opposing_players = [
                    (p1, p2)
                    for p1 in players
                    for p2 in players
                    if (player_team[p1], player_team[p2]) in gw_opp_teams[gw] and (player_type[p1], player_type[p2]) in opposing_positions
                ]
                model.add_constraints((lineup[p1, gw] + lineup[p2, gw] <= 1 for (p1, p2) in opposing_players), name=f"no_opp_{gw}")
    elif options.get("no_opposing_play") == "penalty":
        print("OC - Penalty Opposing Play")
        gw_opp_teams = {
            w: [(f["home"], f["away"]) for f in fixtures if f["gw"] == w] + [(f["away"], f["home"]) for f in fixtures if f["gw"] == w] for w in gws
        }
        if options.get("opposing_play_group") == "all":
            cp_list = [
                (p1, p2, w)
                for p1 in players
                for p2 in players
                for w in gws
                if (player_team[p1], player_team[p2]) in gw_opp_teams[w] and minutes_player_week[p1, w] > 0 and minutes_player_week[p2, w] > 0
            ]
        elif options.get("opposing_play_group", "position") == "position":
            opposing_positions = [(1, 3), (1, 4), (2, 3), (2, 4), (3, 1), (4, 1), (3, 2), (4, 2)]
            cp_list = [
                (p1, p2, w)
                for p1 in players
                for p2 in players
                for w in gws
                if (player_team[p1], player_team[p2]) in gw_opp_teams[w]
                and minutes_player_week[p1, w] > 0
                and minutes_player_week[p2, w] > 0
                and (player_type[p1], player_type[p2]) in opposing_positions
            ]
        cp_pen_var = model.add_variables(cp_list, name="cp_v", vartype=so.binary)
        opposing_play_penalty = options.get("opposing_play_penalty", 0.5)
        cp_penalty = {w: opposing_play_penalty * so.expr_sum(cp_pen_var[p1, p2, w1] for (p1, p2, w1) in cp_list if w1 == w) for w in gws}
        model.add_constraints((lineup[p1, w] + lineup[p2, w] <= 1 + cp_pen_var[p1, p2, w] for (p1, p2, w) in cp_list), name="cp1")
        model.add_constraints((cp_pen_var[p1, p2, w] <= lineup[p1, w] for (p1, p2, w) in cp_list), name="cp2")
        model.add_constraints((cp_pen_var[p1, p2, w] <= lineup[p2, w] for (p1, p2, w) in cp_list), name="cp3")

    if options.get("double_defense_pick") is True:
        print("OC - Double Defense Pick")
        team_players = {t: [p for p in players if player_team[p] == t] for t in teams}
        gk_df_players = {t: [p for p in team_players[t] if player_type[p] in [1, 2]] for t in teams}
        weekly_sum = {(t, w): so.expr_sum(lineup[p, w] for p in gk_df_players[t]) for t in teams for w in gws}
        def_aux = model.add_variables(teams, gws, vartype=so.binary, name="daux")
        model.add_constraints((weekly_sum[t, w] <= 3 * def_aux[t, w] for t in teams for w in gws), name="dauxc1")
        model.add_constraints((weekly_sum[t, w] >= 2 - 3 * (1 - def_aux[t, w]) for t in teams for w in gws), name="dauxc2")

    if options.get("transfer_itb_buffer"):
        buffer_amount = float(options["transfer_itb_buffer"])
        gw_with_tr = model.add_variables(gws, name="gw_with_tr", vartype=so.binary)
        model.add_constraints((SQUAD_SIZE * gw_with_tr[w] >= num_transfers[w] for w in gws), name="gw_with_tr_lb")
        model.add_constraints((gw_with_tr[w] <= num_transfers[w] for w in gws), name="gw_with_tr_ub")
        model.add_constraints((in_the_bank[w] >= buffer_amount * gw_with_tr[w] for w in gws), name="buffer_con")

    if options.get("pick_prices", None) not in [None, {"G": "", "D": "", "M": "", "F": ""}]:
        print("OC - Pick Prices")
        buffer = 0.2
        price_choices = options["pick_prices"]
        for pos, val in price_choices.items():
            if val == "":
                continue
            price_points = [float(i) for i in val.split(",")]
            value_dict = {i: price_points.count(i) for i in set(price_points)}
            con_iter = 0
            for key, count in value_dict.items():
                target_players = [
                    p for p in players if merged_data.loc[p, "Pos"] == pos and buy_price[p] >= key - buffer and buy_price[p] <= key + buffer
                ]
                model.add_constraints((so.expr_sum(squad[p, w] for p in target_players) >= count for w in gws), name=f"price_point_{pos}_{con_iter}")
                con_iter += 1

    if options.get("no_gk_rotation_after", None):
        print("OC - No GK rotation")
        target_gw = int(options["no_gk_rotation_after"])
        players_gk = [p for p in players if player_type[p] == 1]
        model.add_constraints(
            (lineup[p, w] >= lineup[p, target_gw] - use_fh[w] for p in players_gk for w in gws if w > target_gw), name="fixed_lineup_gk"
        )

    if len(options.get("no_chip_gws", [])) > 0:
        print("OC - No Chip GWs")
        no_chip_gws = options["no_chip_gws"]
        model.add_constraint(so.expr_sum(use_bb[w] + use_wc[w] + use_fh[w] for w in no_chip_gws) == 0, name="no_chip_gws")

    if options.get("only_booked_transfers") is True:
        print("OC - Only Booked Transfers")
        forced_in = []
        forced_out = []
        for bt in options.get("booked_transfers", []):
            if bt["gw"] == next_gw:
                if bt.get("transfer_in") is not None:
                    forced_in.append(bt["transfer_in"])
                if bt.get("transfer_out") is not None:
                    forced_out.append(bt["transfer_out"])

        in_players = {(p): 1 if p in forced_in else 0 for p in players}
        out_players = {(p): 1 if p in forced_out else 0 for p in players}
        model.add_constraints((transfer_in[p, next_gw] == in_players[p] for p in players), name="fix_tgw_tr_in")
        model.add_constraints((transfer_out[p, next_gw] == out_players[p] for p in players), name="fix_tgw_tr_out")

    # if options.get('have_2ft_in_gws', None) is not None:
    #     for gw in options['have_2ft_in_gws']:
    #         model.add_constraint(fts[gw] == 2, name=f'have_2ft_{gw}')

    if options.get("force_ft_state_lb", None):
        print("OC - Force FT LB")
        for gw, ft_pos in options["force_ft_state_lb"]:
            model.add_constraint(fts[gw] >= ft_pos, name=f"cft_lb_{gw}")

    if options.get("force_ft_state_ub", None):
        print("OC - Force FT UB")
        for gw, ft_pos in options["force_ft_state_ub"]:
            model.add_constraint(fts[gw] <= ft_pos, name=f"cft_ub_{gw}")

    if options.get("no_trs_except_wc", False) is True:
        print("OC - No TRS except WC")
        model.add_constraints((num_transfers[w] <= SQUAD_SIZE * use_wc[w] for w in gws), name="wc_trs_only")

    # FT gain
    ft_state_value = {}
    for s in ft_states:
        ft_state_value[s] = ft_state_value.get(s - 1, 0) + ft_value_list.get(str(s), ft_value)
    # print(f"Using FT state values of {ft_state_value}")
    print(f"Using FT values of {ft_value_list}")
    gw_ft_value = {w: so.expr_sum(ft_state_value[s] * fts_state[w, s] for s in ft_states) for w in gws}
    gw_ft_gain = {w: gw_ft_value[w] - gw_ft_value.get(w - 1, 0) for w in gws}

    # Objectives
    hit_cost = options.get("hit_cost", 4)
    vcap_weight = options.get("vcap_weight", 0.1)
    gw_xp = {
        w: so.expr_sum(
            points_player_week[p, w]
            * (
                lineup[p, w]
                + captain[p, w]
                + vcap_weight * vicecap[p, w]
                + use_tc[p, w]
                + so.expr_sum(bench_weights[o] * bench[p, w, o] for o in order)
            )
            for p in players
        )
        for w in gws
    }

    gw_total = {
        w: gw_xp[w] - hit_cost * penalized_transfers[w] + gw_ft_gain[w] - ft_penalty[w] + itb_value * in_the_bank[w] - cp_penalty.get(w, 0)
        for w in gws
    }

    if objective == "regular":
        total_xp = so.expr_sum(gw_total[w] for w in gws)
        model.set_objective(-total_xp, sense="N", name="total_regular_xp")
    else:
        decay_objective = so.expr_sum(gw_total[w] * pow(decay_base, w - next_gw) for w in gws)
        model.set_objective(-decay_objective, sense="N", name="total_decay_xp")

    report_decay_base = options.get("report_decay_base", [])
    decay_metrics = {i: so.expr_sum(gw_total[w] * pow(i, w - next_gw) for w in gws) for i in report_decay_base}

    num_iterations = options.get("num_iterations", 1)
    iteration_criteria = options.get("iteration_criteria", "this_gw_transfer_in")
    solutions = []

    for iteration in range(num_iterations):
        mps_file_name = f"tmp/{problem_name}_{problem_id}_{iteration}.mps"
        sol_file_name = f"tmp/{problem_name}_{problem_id}_{iteration}_sol.txt"
        opt_file_name = f"tmp/{problem_name}_{problem_id}_{iteration}.opt"

        tmp_folder = Path() / "tmp"
        tmp_folder.mkdir(exist_ok=True, parents=True)
        model.export_mps(mps_file_name)
        print(f"Exported problem with name: {problem_name}_{problem_id}_{iteration}")

        if options.get("export_debug", False):
            with open("debug.sas", "w") as file:
                file.write(model.to_optmodel())

        # use_cmd = options.get("use_cmd", False)
        solver = options.get("solver", "highs")

        if solver.lower() == "highs":
            # Use highspy Python interface instead of command line
            secs = options.get("secs", 20 * 60)
            presolve = options.get("presolve", "on")
            gap = options.get("gap", 0)
            random_seed = options.get("random_seed", 0)
            verbose = options.get("verbose", False)

            solver_instance = highspy.Highs()
            solver_instance.readModel(str(mps_file_name))
            solver_instance.setOptionValue("parallel", "on")
            solver_instance.setOptionValue("random_seed", random_seed)
            solver_instance.setOptionValue("presolve", presolve)
            solver_instance.setOptionValue("time_limit", secs)
            solver_instance.setOptionValue("mip_rel_gap", gap)
            solver_instance.setOptionValue("log_to_console", verbose)

            solver_instance.run()
            solution = solver_instance.getSolution()
            values = list(solution.col_value)
            for idx, v in enumerate(model.get_variables()):
                v.set_value(values[idx])

        elif solver == "gurobi":
            use_cmd = options.get("use_cmd", False)
            gap = options.get("gap", 0)
            sol_file_name = sol_file_name.replace("_sol", "").replace("txt", "sol")
            command = f"gurobi_cl MIPGap={gap} ResultFile={sol_file_name} {mps_file_name}"

            if use_cmd:
                os.system(command)
            else:

                def print_output(process):
                    while True:
                        output = process.stdout.readline()
                        if "Solving report" in output:
                            time.sleep(2)
                            process.kill()
                        elif output == "" and process.poll() is not None:
                            break
                        elif output:
                            print(output.strip())

                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                output_thread = threading.Thread(target=print_output, args=(process,))
                output_thread.start()
                output_thread.join()

            # Parsing
            with open(sol_file_name) as f:
                for v in model.get_variables():
                    v.set_value(0)
                for line in f:
                    if line[0] == "#":
                        continue
                    if line == "":
                        break
                    words = line.split()
                    v = model.get_variable(words[0])
                    try:
                        if v.get_type() == so.INT:
                            v.set_value(round(float(words[1])))
                        elif v.get_type() == so.BIN:
                            v.set_value(round(float(words[1])))
                        elif v.get_type() == so.CONT:
                            v.set_value(round(float(words[1]), 3))
                    except Exception:
                        print("Error", words[0], line)

        # DataFrame generation
        picks = []
        for w in gws:
            for p in players:
                if squad[p, w].get_value() + squad_fh[p, w].get_value() + transfer_out[p, w].get_value() > BINARY_THRESHOLD:
                    lp = merged_data.loc[p]
                    is_captain = 1 if captain[p, w].get_value() > BINARY_THRESHOLD else 0
                    is_squad = (
                        1
                        if (use_fh[w].get_value() < BINARY_THRESHOLD and squad[p, w].get_value() > BINARY_THRESHOLD)
                        or (use_fh[w].get_value() > BINARY_THRESHOLD and squad_fh[p, w].get_value() > BINARY_THRESHOLD)
                        else 0
                    )
                    is_lineup = 1 if lineup[p, w].get_value() > BINARY_THRESHOLD else 0
                    is_vice = 1 if vicecap[p, w].get_value() > BINARY_THRESHOLD else 0
                    is_tc = 1 if use_tc[p, w].get_value() > BINARY_THRESHOLD else 0
                    is_transfer_in = 1 if transfer_in[p, w].get_value() > BINARY_THRESHOLD else 0
                    is_transfer_out = 1 if transfer_out[p, w].get_value() > BINARY_THRESHOLD else 0
                    bench_value = -1
                    for o in order:
                        if bench[p, w, o].get_value() > BINARY_THRESHOLD:
                            bench_value = o
                    position = type_data.loc[lp["element_type"], "singular_name_short"]
                    player_buy_price = 0 if not is_transfer_in else buy_price[p]
                    player_sell_price = (
                        0
                        if not is_transfer_out
                        else (
                            sell_price[p] if p in price_modified_players and transfer_out_first[p, w].get_value() > BINARY_THRESHOLD else buy_price[p]
                        )
                    )
                    multiplier = 1 * (is_lineup == 1) + 1 * (is_captain == 1) + 1 * (is_tc == 1)
                    xp_cont = points_player_week[p, w] * multiplier

                    # chip
                    if use_wc[w].get_value() > BINARY_THRESHOLD:
                        chip_text = "WC"
                    elif use_fh[w].get_value() > BINARY_THRESHOLD:
                        chip_text = "FH"
                    elif use_bb[w].get_value() > BINARY_THRESHOLD:
                        chip_text = "BB"
                    elif use_tc[p, w].get_value() > BINARY_THRESHOLD:
                        chip_text = "TC"
                    else:
                        chip_text = ""

                    picks.append(
                        {
                            "id": p,
                            "week": w,
                            "name": lp["web_name"],
                            "pos": position,
                            "type": lp["element_type"],
                            "team": lp["name"],
                            "buy_price": player_buy_price,
                            "sell_price": player_sell_price,
                            "xP": round(points_player_week[p, w], 2),
                            "xMin": minutes_player_week[p, w],
                            "squad": is_squad,
                            "lineup": is_lineup,
                            "bench": bench_value,
                            "captain": is_captain,
                            "vicecaptain": is_vice,
                            "transfer_in": is_transfer_in,
                            "transfer_out": is_transfer_out,
                            "multiplier": multiplier,
                            "xp_cont": xp_cont,
                            "chip": chip_text,
                            "iter": iteration,
                            "ft": fts[w].get_value(),
                            "transfer_count": num_transfers[w].get_value(),
                        }
                    )

        picks_df = pd.DataFrame(picks).sort_values(by=["week", "lineup", "type", "xP"], ascending=[True, False, True, True])
        total_xp = so.expr_sum((lineup[p, w] + captain[p, w]) * points_player_week[p, w] for p in players for w in gws).get_value()

        picks_df.sort_values(by=["week", "squad", "lineup", "bench", "type"], ascending=[True, False, False, True, True], inplace=True)

        # Writing summary
        summary_of_actions = ""
        move_summary = {"chip": [], "buy": [], "sell": []}

        # collect statistics
        statistics = {}

        for w in all_gw:
            if w == all_gw[0]:
                statistics[int(w)] = {"itb": in_the_bank[w].get_value(), "ft": fts[w].get_value()}
                continue
            summary_of_actions += f"** GW {w}:\n"
            chip_decision = (
                ("WC" if use_wc[w].get_value() > BINARY_THRESHOLD else "")
                + ("FH" if use_fh[w].get_value() > BINARY_THRESHOLD else "")
                + ("BB" if use_bb[w].get_value() > BINARY_THRESHOLD else "")
                + ("TC" if use_tc_gw[w].get_value() > BINARY_THRESHOLD else "")
            )
            if chip_decision != "":
                summary_of_actions += "CHIP " + chip_decision + "\n"
                move_summary["chip"].append(chip_decision + str(w))
            summary_of_actions += (
                f"ITB={round(in_the_bank[w - 1].get_value(), 1)}->{round(in_the_bank[w].get_value(), 1)}, "
                f"FT={round(fts[w].get_value())}, "
                f"PT={round(penalized_transfers[w].get_value())}, "
                f"NT={round(num_transfers[w].get_value())}\n"
            )
            for p in players:
                if transfer_in[p, w].get_value() > BINARY_THRESHOLD:
                    summary_of_actions += f"Buy {p} - {merged_data['web_name'][p]}\n"
                    if w == next_gw:
                        move_summary["buy"].append(merged_data["web_name"][p])

            for p in players:
                if transfer_out[p, w].get_value() > BINARY_THRESHOLD:
                    summary_of_actions += f"Sell {p} - {merged_data['web_name'][p]}\n"
                    if w == next_gw:
                        move_summary["sell"].append(merged_data["web_name"][p])

            picks_df[picks_df["week"] == w]
            lineup_players = picks_df[(picks_df["week"] == w) & (picks_df["lineup"] == 1)]
            bench_players = picks_df[(picks_df["week"] == w) & (picks_df["bench"] >= 0)]

            # captain_name = picks_df[(picks_df['week'] == w) & (picks_df['captain'] == 1)].iloc[0]['name']
            # vicecap_name = picks_df[(picks_df['week'] == w) & (picks_df['vicecaptain'] == 1)].iloc[0]['name']

            summary_of_actions += "\nLineup: \n"

            def get_display(row):
                return f"{row['name']} ({row['xP']}{', C' if row['captain'] == 1 else ''}{', V' if row['vicecaptain'] == 1 else ''})"

            for typ in [1, 2, 3, 4]:
                type_players = lineup_players[lineup_players["type"] == typ]
                entries = type_players.apply(get_display, axis=1)
                summary_of_actions += "\t" + ", ".join(entries.tolist()) + "\n"
            summary_of_actions += "Bench: \n\t" + ", ".join(bench_players.apply(get_display, axis=1)) + "\n"
            summary_of_actions += "Lineup xPts: " + str(round(lineup_players["xp_cont"].sum(), 2)) + "\n"
            if w != max(gws):
                summary_of_actions += "\n\n"

            statistics[int(w)] = {
                "itb": in_the_bank[w].get_value(),
                "ft": fts[w].get_value(),
                "pt": penalized_transfers[w].get_value(),
                "nt": num_transfers[w].get_value(),
                "xP": lineup_players["xp_cont"].sum(),
                "obj": round(gw_total[w].get_value(), 2),
                "chip": chip_decision if chip_decision != "" else None,
            }

        if options.get("delete_tmp", True):
            time.sleep(0.1)
            try:
                try:
                    os.unlink(mps_file_name)
                except Exception:
                    pass
                try:
                    os.unlink(sol_file_name)
                except Exception:
                    pass
                try:
                    os.unlink(opt_file_name)
                except Exception:
                    pass
            except Exception:
                print("Could not delete temporary files")

        def format_decisions(items):
            return ", ".join(items) if items else "-"

        buy_decisions = format_decisions(move_summary["buy"])
        sell_decisions = format_decisions(move_summary["sell"])
        chip_decisions = format_decisions(move_summary["chip"])

        if options.get("hide_transfers"):
            buy_decisions = sell_decisions = "-"

        # Add current solution to a list, and add a new cut
        solutions.append(
            {
                "iter": iteration,
                "model": model,
                "picks": picks_df,
                "total_xp": total_xp,
                "summary": summary_of_actions,
                "statistics": statistics,
                "buy": buy_decisions,
                "sell": sell_decisions,
                "chip": chip_decisions,
                "score": -model.get_objective_value(),
                "decay_metrics": {key: value.get_value() for key, value in decay_metrics.items()},
            }
        )

        if num_iterations == 1:
            return solutions

        iter_diff = options.get("iteration_difference", 1)

        if iteration_criteria == "this_gw_transfer_in":
            actions = so.expr_sum(
                1 - transfer_in[p, next_gw] for p in players if transfer_in[p, next_gw].get_value() > BINARY_THRESHOLD
            ) + so.expr_sum(transfer_in[p, next_gw] for p in players if transfer_in[p, next_gw].get_value() < BINARY_THRESHOLD)
            model.add_constraint(actions >= 1, name=f"cutoff_{iteration}")

        elif iteration_criteria == "this_gw_transfer_out":
            actions = so.expr_sum(
                1 - transfer_out[p, next_gw] for p in players if transfer_out[p, next_gw].get_value() > BINARY_THRESHOLD
            ) + so.expr_sum(transfer_out[p, next_gw] for p in players if transfer_out[p, next_gw].get_value() < BINARY_THRESHOLD)
            model.add_constraint(actions >= 1, name=f"cutoff_{iteration}")

        elif iteration_criteria == "this_gw_transfer_in_out":
            actions = (
                so.expr_sum(1 - transfer_in[p, next_gw] for p in players if transfer_in[p, next_gw].get_value() > BINARY_THRESHOLD)
                + so.expr_sum(transfer_in[p, next_gw] for p in players if transfer_in[p, next_gw].get_value() < BINARY_THRESHOLD)
                + so.expr_sum(1 - transfer_out[p, next_gw] for p in players if transfer_out[p, next_gw].get_value() > BINARY_THRESHOLD)
                + so.expr_sum(transfer_out[p, next_gw] for p in players if transfer_out[p, next_gw].get_value() < BINARY_THRESHOLD)
            )
            model.add_constraint(actions >= 1, name=f"cutoff_{iteration}")

        elif iteration_criteria == "chip_gws":
            actions = (
                so.expr_sum(1 - use_wc[w] for w in gws if use_wc[w].get_value() > BINARY_THRESHOLD)
                + so.expr_sum(use_wc[w] for w in gws if use_wc[w].get_value() < BINARY_THRESHOLD)
                + so.expr_sum(1 - use_bb[w] for w in gws if use_bb[w].get_value() > BINARY_THRESHOLD)
                + so.expr_sum(use_bb[w] for w in gws if use_bb[w].get_value() < BINARY_THRESHOLD)
                + so.expr_sum(1 - use_fh[w] for w in gws if use_fh[w].get_value() > BINARY_THRESHOLD)
                + so.expr_sum(use_fh[w] for w in gws if use_fh[w].get_value() < BINARY_THRESHOLD)
            )
            model.add_constraint(actions >= 1, name=f"cutoff_{iteration}")

        elif iteration_criteria == "target_gws_transfer_in":
            target_gws = options.get("iteration_target", [next_gw])
            transferred_players = [[p, w] for p in players for w in target_gws if transfer_in[p, w].get_value() > BINARY_THRESHOLD]
            remaining_players = [[p, w] for p in players for w in target_gws if transfer_in[p, w].get_value() < BINARY_THRESHOLD]

            actions = so.expr_sum(1 - transfer_in[p, w] for [p, w] in transferred_players) + so.expr_sum(
                transfer_in[p, w] for [p, w] in remaining_players
            )
            model.add_constraint(actions >= 1, name=f"cutoff_{iteration}")

        elif iteration_criteria == "this_gw_lineup":
            selected_lineup = [p for p in players if lineup[p, next_gw].get_value() > BINARY_THRESHOLD]
            model.add_constraint(
                so.expr_sum(lineup[p, next_gw] for p in selected_lineup) <= len(selected_lineup) - iter_diff, name=f"cutoff_{iteration}"
            )

    return solutions
