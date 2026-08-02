import os
import shlex
import subprocess
import threading
import time
from collections import Counter
from pathlib import Path
from typing import cast

import highspy
import numpy as np
import pandas as pd

from dev.data_parser import read_data
from utils import cached_request, get_random_id

BINARY_THRESHOLD = 0.5  # threshold value for evaluating binary variables
BASE_URL = "https://fantasy.premierleague.com/api"
IS_COLAB = "COLAB_GPU" in os.environ
SQUAD_SIZE = 15
LINEUP_SIZE = 11
MAX_GAMEWEEK = 38
MAX_PLAYERS_PER_TEAM = 3

# HiGHS has no distinct binary type - binaries are integers bounded to [0, 1]
BIN = highspy.HighsVarType.kInteger
INT = highspy.HighsVarType.kInteger
CONT = highspy.HighsVarType.kContinuous


def generate_team_json(team_id, options):
    static_url = f"{BASE_URL}/bootstrap-static/"
    static = cached_request(static_url)
    element_to_type_dict = {x["id"]: x["element_type"] for x in static["elements"]}
    next_gw = next(x for x in static["events"] if x["is_next"])["id"]

    start_prices = {x["id"]: x["now_cost"] - x["cost_change_start"] for x in static["elements"]}

    transfers_url = f"{BASE_URL}/entry/{team_id}/transfers/"
    transfers = cached_request(transfers_url)[::-1]

    history_url = f"{BASE_URL}/entry/{team_id}/history/"
    history = cached_request(history_url)
    chips = history["chips"]
    fh_gws = [x["event"] for x in chips if x["name"] == "freehit"]
    wc_gws = [x["event"] for x in chips if x["name"] == "wildcard"]

    # find out the first gameweek that the user played in - don't assume gw1
    first_gw = history["current"][0]["event"]
    first_gw_url = f"{BASE_URL}/entry/{team_id}/event/{first_gw}/picks/"
    first_gw_data = cached_request(first_gw_url)

    # squad will remain an ID:puchase_price map throughout iteration over transfers
    # once they have been iterated through, can then add on the current selling price
    squad = {x["element"]: start_prices[x["element"]] for x in first_gw_data["picks"]}

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

    fts = calculate_fts(transfers, first_gw, next_gw, fh_gws, wc_gws)
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


def calculate_fts(transfers, first_gw, next_gw, fh_gws, wc_gws):
    n_transfers = dict.fromkeys(range(2, next_gw), 0)
    for t in transfers:
        n_transfers[t["event"]] += 1
    fts = dict.fromkeys(range(first_gw + 1, next_gw + 1), 0)
    fts[first_gw + 1] = 1
    for i in range(first_gw + 2, next_gw + 1):
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
    totals = pd.DataFrame(
        {"total_ev": merged_data[keys].sum(axis=1), "total_min": merged_data[min_keys].sum(axis=1)},
        index=merged_data.index,
    )
    merged_data = pd.concat([merged_data, totals], axis=1)

    merged_data.sort_values(by=["total_ev"], ascending=[False], inplace=True)

    locked_next_gw = [int(i[0]) if isinstance(i, list) else int(i) for i in options.get("locked_next_gw", [])]
    safe_players_due_price = []
    for pos, vals in options.get("pick_prices", {}).items():
        if vals is None or vals == "":
            continue
        price_vals = [float(i) for i in vals.split(",")]
        pp = merged_data[(merged_data["Pos"] == pos) & (merged_data["now_cost"].div(10).isin(price_vals))]["ID"].to_list()
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
        ev_per_price = merged_data["total_ev"].div(merged_data["now_cost"])
        cutoff = ev_per_price.quantile(ev_per_price_cutoff / 100)
        merged_data = merged_data[(ev_per_price > cutoff) | (merged_data["ID"].isin(safe_players))].copy()

    num_players_after = len(merged_data)
    print(f"Filtered player pool from {num_players_before} to {num_players_after} players")

    if options.get("randomized", False):
        rng = np.random.default_rng(seed=options.get("randomization_seed"))
        gws = list(range(gw, min(39, gw + horizon)))
        for w in gws:
            noise = merged_data[f"{w}_Pts"] * (92 - merged_data[f"{w}_xMins"]) / 134 * rng.standard_normal(size=len(merged_data))
            merged_data[f"{w}_Pts"] = merged_data[f"{w}_Pts"] + noise * options.get("randomization_strength", 1)

    type_data = pd.DataFrame(fpl_data["element_types"]).set_index(["id"])

    buy_price = merged_data["now_cost"].div(10).to_dict()
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
    # can only pass the check when using "team_data": "json" or "json_string"
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

    problem_id = get_random_id(5)
    horizon = options.get("horizon", 3)
    objective = options.get("objective", "decay")
    decay_base = options.get("decay_base", 0.84)
    bench_weights = options.get("bench_weights", {0: 0.03, 1: 0.21, 2: 0.06, 3: 0.002})
    bench_weights = {int(key): value for (key, value) in bench_weights.items()}
    ft_value = options.get("ft_value", 1.5)
    ft_value_list = options.get("ft_value_list", {})
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
        initial_ft = 0

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
    m = highspy.Highs()
    sum_ = m.qsum
    verbose = options.get("verbose", False)
    m.setOptionValue("output_flag", bool(verbose))

    def bin_vars(*sets, name):
        return m.addVariables(*sets, lb=0, ub=1, type=BIN, name_prefix=name)

    def int_vars(*sets, name, lb=0, ub=None):
        kwargs = {"lb": lb, "type": INT, "name_prefix": name}
        if ub is not None:
            kwargs["ub"] = ub
        return m.addVariables(*sets, **kwargs)

    def cont_vars(*sets, name, lb=0, ub=None):
        kwargs = {"lb": lb, "type": CONT, "name_prefix": name}
        if ub is not None:
            kwargs["ub"] = ub
        return m.addVariables(*sets, **kwargs)

    # Variables
    squad = bin_vars(players, all_gw, name="squad")
    squad_fh = bin_vars(players, gws, name="squad_fh")
    lineup = bin_vars(players, gws, name="lineup")
    captain = bin_vars(players, gws, name="captain")
    vicecap = bin_vars(players, gws, name="vicecap")
    bench = bin_vars(players, gws, order, name="bench")
    transfer_in = bin_vars(players, gws, name="transfer_in")
    transfer_out_first = bin_vars(price_modified_players, gws, name="tr_out_first")
    transfer_out_regular = bin_vars(players, gws, name="tr_out_reg")
    transfer_out = {
        (p, w): transfer_out_regular[p, w] + (transfer_out_first[p, w] if p in price_modified_players else 0) for p in players for w in gws
    }
    in_the_bank = cont_vars(all_gw, name="itb", lb=0)
    fts = int_vars(all_gw, name="ft", lb=0, ub=5)
    ft_above_ub = bin_vars(gws, name="ft_above")
    ft_below_lb = bin_vars(gws, name="ft_below")
    fts_state = bin_vars(gws, ft_states, name="ft_state")
    penalized_transfers = int_vars(gws, name="pt", lb=0)
    aux = bin_vars(gws, name="aux")
    transfer_count = int_vars(gws, name="trc", lb=0, ub=SQUAD_SIZE)

    use_wc = bin_vars(gws, name="use_wc")
    use_bb = bin_vars(gws, name="use_bb")
    use_fh = bin_vars(gws, name="use_fh")
    use_tc = bin_vars(players, gws, name="use_tc")

    # Dictionaries
    player_type = merged_data["element_type"].to_dict()
    player_pos = merged_data["Pos"].to_dict()
    lineup_type_count = {(t, w): sum_(lineup[p, w] for p in players if player_type[p] == t) for t in el_types for w in gws}
    squad_type_count = {(t, w): sum_(squad[p, w] for p in players if player_type[p] == t) for t in el_types for w in gws}
    squad_fh_type_count = {(t, w): sum_(squad_fh[p, w] for p in players if player_type[p] == t) for t in el_types for w in gws}
    sell_price = data["sell_price"]
    buy_price = data["buy_price"]
    sold_amount = {
        w: (
            sum_(sell_price[p] * transfer_out_first[p, w] for p in price_modified_players)
            + sum_(buy_price[p] * transfer_out_regular[p, w] for p in players)
        )
        for w in gws
    }
    fh_sell_price = {p: sell_price[p] if p in price_modified_players else buy_price[p] for p in players}
    bought_amount = {w: sum_(buy_price[p] * transfer_in[p, w] for p in players) for w in gws}
    pts_by_week = {w: merged_data[f"{w}_Pts"].to_dict() for w in gws}
    xmins_by_week = {w: merged_data[f"{w}_xMins"].to_dict() for w in gws}
    points_player_week = {(p, w): pts_by_week[w][p] for p in players for w in gws}
    minutes_player_week = {(p, w): xmins_by_week[w][p] for p in players for w in gws}
    player_team = merged_data["name"].to_dict()
    squad_count = {w: sum_(squad[p, w] for p in players) for w in gws}
    squad_fh_count = {w: sum_(squad_fh[p, w] for p in players) for w in gws}
    num_transfers = {w: sum_(transfer_out[p, w] for p in players) for w in gws}
    transfer_diff = {w: num_transfers[w] - fts[w] - SQUAD_SIZE * use_wc[w] for w in gws}
    use_tc_gw = {w: sum_(use_tc[p, w] for p in players) for w in gws}

    # Initial conditions
    m.addConstrs([squad[p, next_gw - 1] == 1 for p in initial_squad])
    m.addConstrs([squad[p, next_gw - 1] == 0 for p in players if p not in initial_squad])
    m.addConstr(in_the_bank[next_gw - 1] == itb)
    m.addConstr(fts[next_gw] == initial_ft * (1 - use_wc[next_gw]) + ft_base * use_wc[next_gw])
    m.addConstrs([fts[w] >= 1 for w in gws if w > next_gw])

    # Constraints
    m.addConstrs([squad_count[w] == SQUAD_SIZE for w in gws])
    m.addConstrs([squad_fh_count[w] == SQUAD_SIZE * use_fh[w] for w in gws])
    m.addConstrs([sum_(lineup[p, w] for p in players) == LINEUP_SIZE + (SQUAD_SIZE - LINEUP_SIZE) * use_bb[w] for w in gws])
    m.addConstrs([sum_(bench[p, w, 0] for p in players if player_type[p] == 1) == 1 - use_bb[w] for w in gws])
    m.addConstrs([sum_(bench[p, w, o] for p in players) == 1 - use_bb[w] for w in gws for o in [1, 2, 3]])
    m.addConstrs([sum_(captain[p, w] for p in players) == 1 for w in gws])
    m.addConstrs([sum_(vicecap[p, w] for p in players) == 1 for w in gws])
    m.addConstrs([lineup[p, w] <= squad[p, w] + use_fh[w] for p in players for w in gws])
    m.addConstrs([bench[p, w, o] <= squad[p, w] + use_fh[w] for p in players for w in gws for o in order])
    m.addConstrs([lineup[p, w] <= squad_fh[p, w] + 1 - use_fh[w] for p in players for w in gws])
    m.addConstrs([bench[p, w, o] <= squad_fh[p, w] + 1 - use_fh[w] for p in players for w in gws for o in order])
    m.addConstrs([captain[p, w] <= lineup[p, w] for p in players for w in gws])
    m.addConstrs([vicecap[p, w] <= lineup[p, w] for p in players for w in gws])
    m.addConstrs([captain[p, w] + vicecap[p, w] <= 1 for p in players for w in gws])
    m.addConstrs([lineup[p, w] + sum_(bench[p, w, o] for o in order) <= 1 for p in players for w in gws])
    squad_min_play = type_data["squad_min_play"].astype(int).to_dict()
    squad_max_play = type_data["squad_max_play"].astype(int).to_dict()
    squad_select = type_data["squad_select"].astype(int).to_dict()
    m.addConstrs([lineup_type_count[t, w] >= squad_min_play[t] for t in el_types for w in gws])
    m.addConstrs([lineup_type_count[t, w] <= squad_max_play[t] + use_bb[w] for t in el_types for w in gws])
    m.addConstrs([squad_type_count[t, w] == squad_select[t] for t in el_types for w in gws])
    m.addConstrs([squad_fh_type_count[t, w] == squad_select[t] * use_fh[w] for t in el_types for w in gws])

    if data["max_players_from_team"] > MAX_PLAYERS_PER_TEAM:
        no_transfer = bin_vars(gws, name="no_transfer")
        m.addConstrs([transfer_count[w] <= SQUAD_SIZE * (1 - no_transfer[w]) for w in gws])
        m.addConstrs([transfer_count[w] >= 1 - SQUAD_SIZE * no_transfer[w] for w in gws])
        m.addConstrs([sum_(squad[p, w] for p in players if player_team[p] == t) <= MAX_PLAYERS_PER_TEAM + no_transfer[w] for t in teams for w in gws])
    else:
        m.addConstrs([sum_(squad[p, w] for p in players if player_team[p] == t) <= MAX_PLAYERS_PER_TEAM for t in teams for w in all_gw])

    m.addConstrs([sum_(squad_fh[p, w] for p in players if player_team[p] == t) <= MAX_PLAYERS_PER_TEAM * use_fh[w] for t in teams for w in gws])

    ## Transfer constraints
    m.addConstrs([squad[p, w] == squad[p, w - 1] + transfer_in[p, w] - transfer_out[p, w] for p in players for w in gws])
    m.addConstrs(
        [
            in_the_bank[w]
            == in_the_bank[w - 1] + sold_amount[w] - bought_amount[w] - (transfer_count[w] * itb_loss_per_transfer if w > next_gw else 0)
            for w in gws
        ]
    )
    m.addConstrs(
        [
            sum_(fh_sell_price[p] * squad[p, w - 1] for p in players) + in_the_bank[w - 1] >= sum_(fh_sell_price[p] * squad_fh[p, w] for p in players)
            for w in gws
        ]
    )
    m.addConstrs([transfer_in[p, w] <= 1 - use_fh[w] for p in players for w in gws])
    m.addConstrs([transfer_out[p, w] <= 1 - use_fh[w] for p in players for w in gws])

    ## Free transfer constraints
    # Next week's FTs before clamping: what's left after this week's transfers, plus the 1 earned.
    # A wildcard or free hit cancels that gain out, so the FT count carries over unchanged.
    raw_gw_ft = {w: fts[w] - transfer_count[w] + 1 - use_wc[w] - use_fh[w] for w in gws}
    # Big-M only has to dominate the FT range, which is 0-5 plus transfers already made in current gw.
    big_m = 20

    # ft_above_ub[w] == 1  <=>  raw_gw_ft[w] > 5
    m.addConstrs([raw_gw_ft[w] >= 6 - big_m * (1 - ft_above_ub[w]) for w in gws])
    m.addConstrs([raw_gw_ft[w] <= 5 + big_m * ft_above_ub[w] for w in gws])

    # ft_below_lb[w] == 1  <=>  raw_gw_ft[w] < 1
    m.addConstrs([raw_gw_ft[w] <= 0 + big_m * (1 - ft_below_lb[w]) for w in gws])
    m.addConstrs([raw_gw_ft[w] >= 1 - big_m * ft_below_lb[w] for w in gws])

    # Clamp the raw count into [1, 5] to get the FTs actually available next week.

    # ft_above_ub[w] == 1  =>  fts[w + 1] == 5
    m.addConstrs([fts[w + 1] <= 5 + big_m * (1 - ft_above_ub[w]) for w in gws if w + 1 in gws])
    m.addConstrs([fts[w + 1] >= 5 - big_m * (1 - ft_above_ub[w]) for w in gws if w + 1 in gws])

    # ft_below_lb[w] == 1  =>  fts[w + 1] == 1
    m.addConstrs([fts[w + 1] <= 1 + big_m * (1 - ft_below_lb[w]) for w in gws if w + 1 in gws])
    m.addConstrs([fts[w + 1] >= 1 - big_m * (1 - ft_below_lb[w]) for w in gws if w + 1 in gws])

    # Neither indicator set  =>  fts[w + 1] == raw_gw_ft[w], i.e. the raw count was already in range
    m.addConstrs([fts[w + 1] - raw_gw_ft[w] <= big_m * (ft_above_ub[w] + ft_below_lb[w]) for w in gws if w + 1 in gws])
    m.addConstrs([raw_gw_ft[w] - fts[w + 1] <= big_m * (ft_above_ub[w] + ft_below_lb[w]) for w in gws if w + 1 in gws])

    m.addConstrs([fts[w] == sum_(fts_state[w, s] * s for s in ft_states) for w in gws])
    m.addConstrs([sum_(fts_state[w, s] for s in ft_states) == 1 for w in gws])

    m.addConstrs([penalized_transfers[w] >= transfer_diff[w] for w in gws])

    ## Chip constraints
    m.addConstrs([use_wc[w] + use_fh[w] + use_bb[w] + use_tc_gw[w] <= 1 for w in gws])
    m.addConstrs([aux[w] <= 1 - use_wc[w - 1] for w in gws if w > next_gw])
    m.addConstrs([aux[w] <= 1 - use_fh[w - 1] for w in gws if w > next_gw])
    m.addConstrs([use_tc[p, w] <= captain[p, w] for p in players for w in gws])

    wc = options.get("use_wc", [])
    if len(wc) > 0:
        m.addConstrs([use_wc[w] == 1 for w in wc])
        chip_limits["wc"] = len(wc)

    bb = options.get("use_bb", [])
    if len(bb) > 0:
        m.addConstrs([use_bb[w] == 1 for w in bb])
        chip_limits["bb"] = len(bb)

    fh = options.get("use_fh", [])
    if len(fh) > 0:
        m.addConstrs([use_fh[w] == 1 for w in fh])
        chip_limits["fh"] = len(fh)

    tc = options.get("use_tc", [])
    if len(tc) > 0:
        m.addConstrs([use_tc_gw[w] == 1 for w in tc])
        chip_limits["tc"] = len(tc)

    if len(allowed_chip_gws.get("wc", [])) > 0:
        gws_banned = [w for w in gws if w not in allowed_chip_gws["wc"]]
        m.addConstrs([use_wc[w] == 0 for w in gws_banned])
        chip_limits["wc"] = 1
    if len(allowed_chip_gws.get("fh", [])) > 0:
        gws_banned = [w for w in gws if w not in allowed_chip_gws["fh"]]
        m.addConstrs([use_fh[w] == 0 for w in gws_banned])
        chip_limits["fh"] = 1
    if len(allowed_chip_gws.get("bb", [])) > 0:
        gws_banned = [w for w in gws if w not in allowed_chip_gws["bb"]]
        m.addConstrs([use_bb[w] == 0 for w in gws_banned])
        chip_limits["bb"] = 1
    if len(allowed_chip_gws.get("tc", [])) > 0:
        gws_banned = [w for w in gws if w not in allowed_chip_gws["tc"]]
        m.addConstrs([use_tc_gw[w] == 0 for w in gws_banned])
        chip_limits["tc"] = 1

    if len(forced_chip_gws.get("wc", [])) > 0:
        m.addConstr(sum_(use_wc[w] for w in forced_chip_gws["wc"]) == 1)
        chip_limits["wc"] = 1
    if len(forced_chip_gws.get("fh", [])) > 0:
        m.addConstr(sum_(use_fh[w] for w in forced_chip_gws["fh"]) == 1)
        chip_limits["fh"] = 1
    if len(forced_chip_gws.get("bb", [])) > 0:
        m.addConstr(sum_(use_bb[w] for w in forced_chip_gws["bb"]) == 1)
        chip_limits["bb"] = 1
    if len(forced_chip_gws.get("tc", [])) > 0:
        m.addConstr(sum_(use_tc_gw[w] for w in forced_chip_gws["tc"]) == 1)
        chip_limits["tc"] = 1

    m.addConstr(sum_(use_wc[w] for w in gws) <= chip_limits.get("wc", 0))
    m.addConstr(sum_(use_bb[w] for w in gws) <= chip_limits.get("bb", 0))
    m.addConstr(sum_(use_fh[w] for w in gws) <= chip_limits.get("fh", 0))
    m.addConstr(sum_(use_tc_gw[w] for w in gws) <= chip_limits.get("tc", 0))
    m.addConstrs([squad_fh[p, w] <= use_fh[w] for p in players for w in gws])

    ## Multiple-sell fix
    m.addConstrs([transfer_out_first[p, w] + transfer_out_regular[p, w] <= 1 for p in price_modified_players for w in gws])
    m.addConstrs(
        [
            horizon * sum_(transfer_out_first[p, w] for w in gws if w <= wbar) >= sum_(transfer_out_regular[p, w] for w in gws if w >= wbar)
            for p in price_modified_players
            for wbar in gws
        ]
    )
    m.addConstrs([sum_(transfer_out_first[p, w] for w in gws) <= 1 for p in price_modified_players])

    ## Transfer in/out fix
    m.addConstrs([transfer_in[p, w] + transfer_out[p, w] <= 1 for p in players for w in gws])

    ## Tr Count Constraints
    ft_penalty = dict.fromkeys(gws, 0)
    m.addConstrs([transfer_count[w] >= num_transfers[w] - SQUAD_SIZE * use_wc[w] for w in gws])
    m.addConstrs([transfer_count[w] <= num_transfers[w] for w in gws])
    m.addConstrs([transfer_count[w] <= SQUAD_SIZE * (1 - use_wc[w]) for w in gws])
    if ft_use_penalty is not None:
        ft_penalty = {w: ft_use_penalty * transfer_count[w] for w in gws}

    ## Optional constraints
    if options.get("banned", None):
        print("OC - Banned")
        banned_players = options["banned"]
        m.addConstrs([sum_(squad[p, w] for w in gws) == 0 for p in banned_players if p in players])
        m.addConstrs([sum_(squad_fh[p, w] for w in gws) == 0 for p in banned_players if p in players])

    if options.get("banned_next_gw", None):
        print("OC - Banned Next GW")
        banned_in_gw = [(x, gws[0]) if isinstance(x, int) else tuple(x) for x in options["banned_next_gw"]]
        m.addConstrs([squad[p0, p1] == 0 for (p0, p1) in banned_in_gw if p0 in players])
        m.addConstrs([squad_fh[p0, p1] == 0 for (p0, p1) in banned_in_gw if p0 in players])

    if options.get("locked", None):
        print("OC - Locked")
        locked_players = options["locked"]
        m.addConstrs([squad[p, w] + squad_fh[p, w] == 1 for p in locked_players for w in gws])

    if options.get("locked_next_gw", None):
        print("OC - Locked Next GW")
        locked_in_gw = [(x, gws[0]) if isinstance(x, int) else tuple(x) for x in options["locked_next_gw"]]
        m.addConstrs([squad[p0, p1] == 1 for (p0, p1) in locked_in_gw])

    if options.get("no_future_transfer", None):
        print("OC - No Future Tr")
        m.addConstr(sum_(transfer_in[p, w] for p in players for w in gws if w > next_gw and w != options.get("use_wc")) == 0)

    if options.get("no_transfer_last_gws", None):
        print("OC - No TR last GWs")
        no_tr_gws = options["no_transfer_last_gws"]
        if horizon > no_tr_gws:
            m.addConstrs([sum_(transfer_in[p, w] for p in players) <= SQUAD_SIZE * use_wc[w] for w in gws if w > last_gw - no_tr_gws])

    if options.get("num_transfers", None) is not None:
        print("OC - Num Transfers")
        m.addConstr(sum_(transfer_in[p, next_gw] for p in players) == options["num_transfers"])

    if options.get("hit_limit", None):
        print("OC - Hit Limit")
        m.addConstr(sum_(penalized_transfers[w] for w in gws) <= int(options["hit_limit"]))

    if options.get("weekly_hit_limit") is not None:
        weekly_hit_limit = int(options.get("weekly_hit_limit"))
        m.addConstrs([penalized_transfers[w] <= weekly_hit_limit for w in gws])

    # if options.get("ft_custom_value", None) is not None:
    #     ft_custom_value = {int(key): value for (key, value) in options.get('ft_custom_value', {}).items()}
    #     ft_gw_value = {**{gw: ft_value for gw in gws}, **ft_custom_value}

    if options.get("future_transfer_limit", None):
        print("OC - Future TR Limit")
        m.addConstr(
            sum_(transfer_in[p, w] for p in players for w in gws if w > next_gw and w not in options.get("use_wc", []))
            <= options["future_transfer_limit"]
        )

    if options.get("no_transfer_gws", None):
        print("OC - No TR GWs")
        if len(options["no_transfer_gws"]) > 0:
            m.addConstr(sum_(transfer_in[p, w] for p in players for w in options["no_transfer_gws"]) == 0)

    if options.get("no_transfer_by_position", None):
        print("OC - No TR by position")
        if len(options["no_transfer_by_position"]) > 0:
            m.addConstrs(
                [transfer_in[p, w] <= use_wc[w] for p in players for w in gws if w > 1 if player_pos[p] in options["no_transfer_by_position"]]
            )

    max_defs_per_team = options.get("max_defenders_per_team", 3)
    if max_defs_per_team < MAX_PLAYERS_PER_TEAM:
        m.addConstrs(
            [
                sum_(squad[p, w] for p in players if player_team[p] == t and player_pos[p] in {"G", "D"}) <= max_defs_per_team
                for t in teams
                for w in gws
            ]
        )
        m.addConstrs(
            [
                sum_(squad_fh[p, w] for p in players if player_team[p] == t and player_pos[p] in {"G", "D"}) <= max_defs_per_team * use_fh[w]
                for t in teams
                for w in gws
            ]
        )

    for booked_transfer in booked_transfers:
        print("OC - Booked TRs")
        transfer_gw = booked_transfer.get("gw", None)
        if transfer_gw is None:
            continue
        player_in = booked_transfer.get("transfer_in", None)
        player_out = booked_transfer.get("transfer_out", None)
        if player_in is not None:
            m.addConstr(transfer_in[player_in, transfer_gw] == 1)
        if player_out is not None:
            m.addConstr(transfer_out[player_out, transfer_gw] == 1)

    cp_penalty = {}
    if options.get("no_opposing_play") is True:
        print("OC - No Opposing Play")
        gw_opp_teams = {
            w: [(f["home"], f["away"]) for f in fixtures if f["gw"] == w] + [(f["away"], f["home"]) for f in fixtures if f["gw"] == w] for w in gws
        }
        for gw in gws:
            if options.get("opposing_play_group", "all") == "all":
                opposing_players = [(p1, p2) for p1 in players for p2 in players if (player_team[p1], player_team[p2]) in gw_opp_teams[gw]]
                m.addConstrs([lineup[p1, gw] + lineup[p2, gw] <= 1 for (p1, p2) in opposing_players])
            elif options.get("opposing_play_group") == "position":
                opposing_positions = [(1, 3), (1, 4), (2, 3), (2, 4), (3, 1), (4, 1), (3, 2), (4, 2)]
                opposing_players = [
                    (p1, p2)
                    for p1 in players
                    for p2 in players
                    if (player_team[p1], player_team[p2]) in gw_opp_teams[gw] and (player_type[p1], player_type[p2]) in opposing_positions
                ]
                m.addConstrs([lineup[p1, gw] + lineup[p2, gw] <= 1 for (p1, p2) in opposing_players])
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
        else:
            cp_list = []
        cp_pen_var = bin_vars(cp_list, name="cp_v")
        opposing_play_penalty = options.get("opposing_play_penalty", 0.5)
        cp_penalty = {w: opposing_play_penalty * sum_(cp_pen_var[p1, p2, w1] for (p1, p2, w1) in cp_list if w1 == w) for w in gws}
        m.addConstrs([lineup[p1, w] + lineup[p2, w] <= 1 + cp_pen_var[p1, p2, w] for (p1, p2, w) in cp_list])
        m.addConstrs([cp_pen_var[p1, p2, w] <= lineup[p1, w] for (p1, p2, w) in cp_list])
        m.addConstrs([cp_pen_var[p1, p2, w] <= lineup[p2, w] for (p1, p2, w) in cp_list])

    if options.get("double_defense_pick") is True:
        print("OC - Double Defense Pick")
        team_players = {t: [p for p in players if player_team[p] == t] for t in teams}
        gk_df_players = {t: [p for p in team_players[t] if player_type[p] in [1, 2]] for t in teams}
        weekly_sum = {(t, w): sum_(lineup[p, w] for p in gk_df_players[t]) for t in teams for w in gws}
        def_aux = bin_vars(teams, gws, name="daux")
        m.addConstrs([weekly_sum[t, w] <= 3 * def_aux[t, w] for t in teams for w in gws])
        m.addConstrs([weekly_sum[t, w] >= 2 - 3 * (1 - def_aux[t, w]) for t in teams for w in gws])

    if options.get("transfer_itb_buffer"):
        buffer_amount = float(options["transfer_itb_buffer"])
        gw_with_tr = bin_vars(gws, name="gw_with_tr")
        m.addConstrs([SQUAD_SIZE * gw_with_tr[w] >= num_transfers[w] for w in gws])
        m.addConstrs([gw_with_tr[w] <= num_transfers[w] for w in gws])
        m.addConstrs([in_the_bank[w] >= buffer_amount * gw_with_tr[w] for w in gws])

    if options.get("pick_prices", None) not in [None, {"G": "", "D": "", "M": "", "F": ""}]:
        print("OC - Pick Prices")
        buffer = 0.2
        price_choices = options["pick_prices"]
        for pos, price_val in price_choices.items():
            if price_val == "":
                continue
            price_points = [float(i) for i in price_val.split(",")]
            value_dict = {i: price_points.count(i) for i in set(price_points)}
            for key, count in value_dict.items():
                target_players = [p for p in players if player_pos[p] == pos and buy_price[p] >= key - buffer and buy_price[p] <= key + buffer]
                m.addConstrs([sum_(squad[p, w] for p in target_players) >= count for w in gws])

    if options.get("no_gk_rotation_after", None):
        print("OC - No GK rotation")
        target_gw = int(options["no_gk_rotation_after"])
        players_gk = [p for p in players if player_type[p] == 1]
        m.addConstrs([lineup[p, w] >= lineup[p, target_gw] - use_fh[w] for p in players_gk for w in gws if w > target_gw])

    if len(options.get("no_chip_gws", [])) > 0:
        print("OC - No Chip GWs")
        no_chip_gws = options["no_chip_gws"]
        m.addConstr(sum_(use_bb[w] + use_wc[w] + use_fh[w] for w in no_chip_gws) == 0)

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
        m.addConstrs([transfer_in[p, next_gw] == in_players[p] for p in players])
        m.addConstrs([transfer_out[p, next_gw] == out_players[p] for p in players])

    # if options.get('have_2ft_in_gws', None) is not None:
    #     for gw in options['have_2ft_in_gws']:
    #         m.addConstr(fts[gw] == 2)

    if options.get("force_ft_state_lb", None):
        print("OC - Force FT LB")
        for gw, ft_pos in options["force_ft_state_lb"]:
            m.addConstr(fts[gw] >= ft_pos)

    if options.get("force_ft_state_ub", None):
        print("OC - Force FT UB")
        for gw, ft_pos in options["force_ft_state_ub"]:
            m.addConstr(fts[gw] <= ft_pos)

    if options.get("no_trs_except_wc", False) is True:
        print("OC - No TRS except WC")
        m.addConstrs([num_transfers[w] <= SQUAD_SIZE * use_wc[w] for w in gws])

    # FT gain
    ft_state_value = {}
    for s in ft_states:
        ft_state_value[s] = ft_state_value.get(s - 1, 0) + ft_value_list.get(str(s), ft_value)
    # print(f"Using FT state values of {ft_state_value}")
    print(f"Using FT values of {ft_value_list}")
    gw_ft_value = {w: sum_(ft_state_value[s] * fts_state[w, s] for s in ft_states) for w in gws}
    gw_ft_gain = {w: gw_ft_value[w] - gw_ft_value.get(w - 1, 0) for w in gws}

    # Objectives
    hit_cost = options.get("hit_cost", 4)
    vcap_weight = options.get("vcap_weight", 0.1)
    gw_xp = {
        w: sum_(
            points_player_week[p, w]
            * (lineup[p, w] + captain[p, w] + vcap_weight * vicecap[p, w] + use_tc[p, w] + sum_(bench_weights[o] * bench[p, w, o] for o in order))
            for p in players
        )
        for w in gws
    }

    gw_total = {
        w: gw_xp[w] - hit_cost * penalized_transfers[w] + gw_ft_gain[w] - ft_penalty[w] + itb_value * in_the_bank[w] - cp_penalty.get(w, 0)
        for w in gws
    }

    if objective == "regular":
        objective_expr = sum_(gw_total[w] for w in gws)
    else:
        objective_expr = sum_(gw_total[w] * pow(decay_base, w - next_gw) for w in gws)
    m.setObjective(objective_expr, sense=highspy.ObjSense.kMaximize)

    report_decay_base = options.get("report_decay_base", [])
    decay_metrics = {i: sum_(gw_total[w] * pow(i, w - next_gw) for w in gws) for i in report_decay_base}

    num_iterations = options.get("num_iterations", 1)
    iteration_criteria = options.get("iteration_criteria", "this_gw_transfer_in")

    if iteration_criteria in {"this_gw_transfer_in", "this_gw_transfer_in_out"} and next_gw in options.get("use_fh", []):
        iteration_criteria = "this_gw_lineup"
    solutions = []

    secs = options.get("secs", 20 * 60)
    presolve = options.get("presolve", "on")
    gap = options.get("gap", 0)
    random_seed = options.get("random_seed", 0)
    solver = options.get("solver", "highs")

    m.setOptionValue("parallel", "on")
    m.setOptionValue("random_seed", random_seed)
    m.setOptionValue("presolve", presolve)
    m.setOptionValue("time_limit", secs)
    m.setOptionValue("mip_rel_gap", gap)
    m.setOptionValue("log_to_console", verbose)

    if solver == "gurobi":
        var_names = [m.variableName(i) for i in range(m.numVariables)]

    def make_val(values_array):
        def val(x):
            if isinstance(x, (int, float)):
                return float(x)
            if isinstance(x, highspy.highs_var):
                return values_array[x.index]
            return x.evaluate(values_array)

        return val

    def run_solve(iteration):
        if solver != "gurobi":
            m.run()
            # read the solution vector once - m.val re-copies it out of HiGHS on every single call
            return make_val(list(m.getSolution().col_value))

        tmp_folder = Path() / "tmp"
        tmp_folder.mkdir(exist_ok=True, parents=True)
        mps_file_name = f"tmp/{problem_name}_{problem_id}_{iteration}.mps"
        sol_file_name = f"tmp/{problem_name}_{problem_id}_{iteration}.sol"
        m.writeModel(mps_file_name)
        command = f"gurobi_cl MIPGap={gap} ResultFile={sol_file_name} {mps_file_name}"

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

        process = subprocess.Popen(shlex.split(command), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        output_thread = threading.Thread(target=print_output, args=(process,))
        output_thread.start()
        output_thread.join()

        values_by_name = {}
        with open(sol_file_name) as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                name, value = line.split()
                values_by_name[name] = float(value)
        values_array = [values_by_name.get(n, 0.0) for n in var_names]

        if options.get("delete_tmp", True):
            for fname in (mps_file_name, sol_file_name):
                try:
                    os.unlink(fname)
                except OSError:
                    pass

        return make_val(values_array)

    for iteration in range(num_iterations):
        val = run_solve(iteration)

        # DataFrame generation
        picks = []
        for w in gws:
            for p in players:
                if val(squad[p, w]) + val(squad_fh[p, w]) + val(transfer_out[p, w]) > BINARY_THRESHOLD:
                    lp = merged_data.loc[p]
                    is_captain = 1 if val(captain[p, w]) > BINARY_THRESHOLD else 0
                    is_squad = (
                        1
                        if (val(use_fh[w]) < BINARY_THRESHOLD and val(squad[p, w]) > BINARY_THRESHOLD)
                        or (val(use_fh[w]) > BINARY_THRESHOLD and val(squad_fh[p, w]) > BINARY_THRESHOLD)
                        else 0
                    )
                    is_lineup = 1 if val(lineup[p, w]) > BINARY_THRESHOLD else 0
                    is_vice = 1 if val(vicecap[p, w]) > BINARY_THRESHOLD else 0
                    is_tc = 1 if val(use_tc[p, w]) > BINARY_THRESHOLD else 0
                    is_transfer_in = 1 if val(transfer_in[p, w]) > BINARY_THRESHOLD else 0
                    is_transfer_out = 1 if val(transfer_out[p, w]) > BINARY_THRESHOLD else 0
                    bench_value = -1
                    for o in order:
                        if val(bench[p, w, o]) > BINARY_THRESHOLD:
                            bench_value = o
                    position = type_data.loc[lp["element_type"], "singular_name_short"]
                    player_buy_price = 0 if not is_transfer_in else buy_price[p]
                    player_sell_price = (
                        0
                        if not is_transfer_out
                        else (sell_price[p] if p in price_modified_players and val(transfer_out_first[p, w]) > BINARY_THRESHOLD else buy_price[p])
                    )
                    multiplier = 1 * (is_lineup == 1) + 1 * (is_captain == 1) + 1 * (is_tc == 1)
                    xp_cont = points_player_week[p, w] * multiplier

                    if val(use_wc[w]) > BINARY_THRESHOLD:
                        chip_text = "WC"
                    elif val(use_fh[w]) > BINARY_THRESHOLD:
                        chip_text = "FH"
                    elif val(use_bb[w]) > BINARY_THRESHOLD:
                        chip_text = "BB"
                    elif val(use_tc[p, w]) > BINARY_THRESHOLD:
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
                            "ft": val(fts[w]),
                            "transfer_count": val(num_transfers[w]),
                        }
                    )

        picks_df = pd.DataFrame(picks).sort_values(by=["week", "lineup", "type", "xP"], ascending=[True, False, True, True])
        total_xp = val(sum_((lineup[p, w] + captain[p, w]) * points_player_week[p, w] for p in players for w in gws))

        picks_df.sort_values(by=["week", "squad", "lineup", "bench", "type"], ascending=[True, False, False, True, True], inplace=True)

        summary_of_actions = ""
        move_summary = {"chip": [], "buy": [], "sell": []}
        statistics = {}

        for w in all_gw:
            if w == all_gw[0]:
                statistics[int(w)] = {"itb": val(in_the_bank[w]), "ft": val(fts[w])}
                continue
            summary_of_actions += f"** GW {w}:\n"
            chip_decision = (
                ("WC" if val(use_wc[w]) > BINARY_THRESHOLD else "")
                + ("FH" if val(use_fh[w]) > BINARY_THRESHOLD else "")
                + ("BB" if val(use_bb[w]) > BINARY_THRESHOLD else "")
                + ("TC" if val(use_tc_gw[w]) > BINARY_THRESHOLD else "")
            )
            if chip_decision != "":
                summary_of_actions += "CHIP " + chip_decision + "\n"
                move_summary["chip"].append(chip_decision + str(w))
            summary_of_actions += (
                f"ITB={round(val(in_the_bank[w - 1]), 1)}->{round(val(in_the_bank[w]), 1)}, "
                f"FT={round(val(fts[w]))}, "
                f"PT={round(val(penalized_transfers[w]))}, "
                f"NT={round(val(num_transfers[w]))}\n"
            )
            for p in players:
                if val(transfer_in[p, w]) > BINARY_THRESHOLD:
                    summary_of_actions += f"Buy {p} - {merged_data['web_name'][p]}\n"
                    if w == next_gw:
                        move_summary["buy"].append(merged_data["web_name"][p])

            for p in players:
                if val(transfer_out[p, w]) > BINARY_THRESHOLD:
                    summary_of_actions += f"Sell {p} - {merged_data['web_name'][p]}\n"
                    if w == next_gw:
                        move_summary["sell"].append(merged_data["web_name"][p])

            lineup_players = picks_df[(picks_df["week"] == w) & (picks_df["lineup"] == 1)]
            bench_players = picks_df[(picks_df["week"] == w) & (picks_df["bench"] >= 0)]

            summary_of_actions += "\nLineup: \n"

            def get_display(row):
                return f"{row['name']} ({row['xP']}{', C' if row['captain'] == 1 else ''}{', V' if row['vicecaptain'] == 1 else ''})"

            for typ in [1, 2, 3, 4]:
                type_players = cast(pd.DataFrame, lineup_players[lineup_players["type"] == typ])
                entries = type_players.apply(get_display, axis=1)
                summary_of_actions += "\t" + ", ".join(entries.tolist()) + "\n"
            summary_of_actions += "Bench: \n\t" + ", ".join(bench_players.apply(get_display, axis=1)) + "\n"
            summary_of_actions += "Lineup xPts: " + str(round(lineup_players["xp_cont"].sum(), 2)) + "\n"
            if w != max(gws):
                summary_of_actions += "\n\n"

            statistics[int(w)] = {
                "itb": val(in_the_bank[w]),
                "ft": val(fts[w]),
                "pt": val(penalized_transfers[w]),
                "nt": val(num_transfers[w]),
                "xP": lineup_players["xp_cont"].sum(),
                "obj": round(val(gw_total[w]), 2),
                "chip": chip_decision if chip_decision != "" else None,
            }

        def format_decisions(items):
            return ", ".join(items) if items else "-"

        buy_decisions = format_decisions(move_summary["buy"])
        sell_decisions = format_decisions(move_summary["sell"])
        chip_decisions = format_decisions(move_summary["chip"])

        if options.get("hide_transfers"):
            buy_decisions = sell_decisions = "-"

        solutions.append(
            {
                "iter": iteration,
                "picks": picks_df,
                "total_xp": total_xp,
                "summary": summary_of_actions,
                "statistics": statistics,
                "buy": buy_decisions,
                "sell": sell_decisions,
                "chip": chip_decisions,
                "score": val(objective_expr),
                "decay_metrics": {key: val(value) for key, value in decay_metrics.items()},
            }
        )

        if num_iterations == 1:
            return solutions

        iter_diff = options.get("iteration_difference", 1)

        if iteration_criteria == "this_gw_transfer_in":
            actions = sum_(1 - transfer_in[p, next_gw] for p in players if val(transfer_in[p, next_gw]) > BINARY_THRESHOLD) + sum_(
                transfer_in[p, next_gw] for p in players if val(transfer_in[p, next_gw]) < BINARY_THRESHOLD
            )
            m.addConstr(actions >= 1)

        elif iteration_criteria == "this_gw_transfer_out":
            actions = sum_(1 - transfer_out[p, next_gw] for p in players if val(transfer_out[p, next_gw]) > BINARY_THRESHOLD) + sum_(
                transfer_out[p, next_gw] for p in players if val(transfer_out[p, next_gw]) < BINARY_THRESHOLD
            )
            m.addConstr(actions >= 1)

        elif iteration_criteria == "this_gw_transfer_in_out":
            actions = (
                sum_(1 - transfer_in[p, next_gw] for p in players if val(transfer_in[p, next_gw]) > BINARY_THRESHOLD)
                + sum_(transfer_in[p, next_gw] for p in players if val(transfer_in[p, next_gw]) < BINARY_THRESHOLD)
                + sum_(1 - transfer_out[p, next_gw] for p in players if val(transfer_out[p, next_gw]) > BINARY_THRESHOLD)
                + sum_(transfer_out[p, next_gw] for p in players if val(transfer_out[p, next_gw]) < BINARY_THRESHOLD)
            )
            m.addConstr(actions >= 1)

        elif iteration_criteria == "chip_gws":
            actions = (
                sum_(1 - use_wc[w] for w in gws if val(use_wc[w]) > BINARY_THRESHOLD)
                + sum_(use_wc[w] for w in gws if val(use_wc[w]) < BINARY_THRESHOLD)
                + sum_(1 - use_bb[w] for w in gws if val(use_bb[w]) > BINARY_THRESHOLD)
                + sum_(use_bb[w] for w in gws if val(use_bb[w]) < BINARY_THRESHOLD)
                + sum_(1 - use_fh[w] for w in gws if val(use_fh[w]) > BINARY_THRESHOLD)
                + sum_(use_fh[w] for w in gws if val(use_fh[w]) < BINARY_THRESHOLD)
            )
            m.addConstr(actions >= 1)

        elif iteration_criteria == "target_gws_transfer_in":
            target_gws = options.get("iteration_target", [next_gw])
            transferred_players = [[p, w] for p in players for w in target_gws if val(transfer_in[p, w]) > BINARY_THRESHOLD]
            remaining_players = [[p, w] for p in players for w in target_gws if val(transfer_in[p, w]) < BINARY_THRESHOLD]

            actions = sum_(1 - transfer_in[p, w] for [p, w] in transferred_players) + sum_(transfer_in[p, w] for [p, w] in remaining_players)
            m.addConstr(actions >= 1)

        elif iteration_criteria == "this_gw_lineup":
            selected_lineup = [p for p in players if val(lineup[p, next_gw]) > BINARY_THRESHOLD]
            m.addConstr(sum_(lineup[p, next_gw] for p in selected_lineup) <= len(selected_lineup) - iter_diff)

    return solutions
