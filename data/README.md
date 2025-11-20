## Setting Explanations

This file documents all configurable settings for the FPL solver. Settings are organized by complexity:

- **[User-Friendly Settings](#user-friendly-settings)** – Essential options for most users (see `user_settings.json`)
- **[Advanced Settings](#advanced-settings)** – Fine-tuning options (see `comprehensive_settings.json`)
- **[Complete Reference](#complete-reference)** – Full alphabetical listing of all settings

---

## User-Friendly Settings

These are the core settings most users will interact with. You'll find them in `user_settings.json`.

### Planning Horizon
- `horizon`: length of the planning horizon (number of gameweeks to optimize)
  - Example: `"horizon": 4` (plan 4 gameweeks ahead)

### Decay & Valuation
- `decay_base`: value assigned to decay rate of expected points (discounts future GWs)
  - Example: `"decay_base": 0.9` (10% discount per GW)
- `ft_value_list`: values of rolling free transfers in different states
  - Example: `"ft_value_list": {"2": 2, "3": 1.6, "4": 1.3, "5": 1.1}` assigns value 2.0 for rolling from 1FT to 2FTs, 1.6 for rolling from 2FTs to 3FTs, etc.

### Data Source & Team
- `datasource`: specifies which projection CSV to use or `"mixed"` for multiple sources
  - Example: `"datasource": "fplreview"` or `"solio"`
- `team_data`: how to provide team data (`"id"` for team_id, `"json"` for inline JSON, or default uses `team.json`)
  - Example: `"team_data": "id"`
- `team_id`: your FPL team ID (requires `team_data: "id"`)
  - Example: `"team_id": 2211381`

### Player Pool Filtering
- `xmin_lb`: drop players below this many expected minutes across the horizon
  - Example: `"xmin_lb": 300` (drop players with &lt;300 expected minutes)
- `ev_per_price_cutoff`: drop players below this percentile of expected value per price
  - Example: `"ev_per_price_cutoff": 30` (drop bottom 30%)
- `keep_top_ev_percent`: force the filtering to always keep the top n% of players by total expected value, even if they would have been otherwise filtered out by other steps
  - Example: `"keep_top_ev_percent": 5` (avoid filtering out players in the top 5% by total EV)

### Player Constraints
- `banned`: list of player IDs to exclude from entire horizon
  - Example: `"banned": []`
- `locked`: list of player IDs to always keep in squad
  - Example: `"locked": [430]` (always include player 430 (Haaland))

### Transfer Constraints
- `no_transfer_last_gws`: number of gameweeks at end where transfers are banned
  - Example: `"no_transfer_last_gws": 0`

### Chips
- `use_wc`: list of gameweeks to use Wildcard
  - Example: `"use_wc": []` or `"use_wc": [15]`
- `use_bb`: list of gameweeks to use Bench Boost
  - Example: `"use_bb": [25]`
- `use_fh`: list of gameweeks to use Free Hit
  - Example: `"use_fh": []`
- `use_tc`: list of gameweeks to use Triple Captain
  - Example: `"use_tc": []`

### Output
- `verbose`: whether to print solver progress to screen
  - Example: `"verbose": true`

---

## Advanced Settings

These settings provide fine-grained control over the optimization. Most users won't need to adjust these. See `comprehensive_settings.json` for default values.

### Scoring & Objective Function
- `ft_value`: value (in points) assigned to having one extra free transfer
  - Example: `"ft_value": 1.5`
- `bench_weights`: weights for each bench position's expected points (0=subGK, 1=sub1, 2=sub2, 3=sub3)
  - Example: `"bench_weights": {"0": 0.03, "1": 0.21, "2": 0.06, "3": 0.002}`
- `vcap_weight`: weight for vice-captain points in the objective function
  - Example: `"vcap_weight": 0.1`
- `itb_value`: value (in points) assigned to having 1.0 extra budget in the bank
  - Example: `"itb_value": 0.08`
- `itb_loss_per_transfer`: reduction in ITB value per scheduled transfer in future (tries to give some budget flexibility for future gameweeks)
  - Example: `"itb_loss_per_transfer": 0.05`
- `ft_use_penalty`: penalty applied when a free transfer is used (prevents trivial scheduled transfers)
  - Example: `"ft_use_penalty": 0.2`

### Transfer & Hit Management
- `no_future_transfer`: if `true`, disable planning transfers beyond current gameweek
  - Example: `"no_future_transfer": false`
- `no_transfer_by_position`: list of positions to ban transfers in/out. Valid: `["G", "D", "M", "F"]`
  - Example: `"no_transfer_by_position": ["G", "D"]`
- `force_ft_state_lb`: list of `[GW, minimum_FTs]` pairs to force minimum FTs in specific gameweeks
  - Example: `"force_ft_state_lb": [[4, 3], [7, 2]]` ensures at least 3 FTs in GW4 and 2 FTs in GW7
- `force_ft_state_ub`: list of `[GW, maximum_FTs]` pairs to force maximum FTs in specific gameweeks
  - Example: `"force_ft_state_ub": [[4, 4], [7, 3]]` ensures at most 4 FTs in GW4 and 3 FTs in GW7
- `num_transfers`: fixed number of transfers for this gameweek (optional override)
  - Example: `"num_transfers": null`
- `hit_limit`: maximum total hits allowed across entire horizon
  - Example: `"hit_limit": null` (no limit)
- `weekly_hit_limit`: maximum hits allowed in a single gameweek
  - Example: `"weekly_hit_limit": 0`
- `hit_cost`: points deducted per hit (default 4)
  - Example: `"hit_cost": 4`
- `future_transfer_limit`: upper bound on total transfers made in future gameweeks
  - Example: `"future_transfer_limit": 5`
- `no_transfer_gws`: list of gameweek numbers where transfers are not allowed
  - Example: `"no_transfer_gws": []`
- `transfer_itb_buffer`: minimum ITB (in the bank) to maintain if any transfer is planned (for robustness)
  - Example: `"transfer_itb_buffer": null` or `0.1` to leave £0.1m in bank
- `booked_transfers`: pre-scheduled transfers for future gameweeks
  - Format: `[{"gw": 5, "transfer_in": 427}, {"gw": 7, "transfer_out": 427}]` (buy player 427 on GW5, sell on GW7)
- `only_booked_transfers`: if `true`, next GW can only use booked transfers
  - Example: `"only_booked_transfers": false`
- `no_trs_except_wc`: if `true`, prevent transfers except via Wildcard
  - Example: `"no_trs_except_wc": false`

### Player Management (Advanced)
- `banned_next_gw`: list of player IDs to ban from next gameweek, or `[ID, GW]` to ban for specific GW
  - Example: `"banned_next_gw": [100, [200, 32]]` bans player ID 100 next GW, and player ID 200 for GW32 only
- `locked_next_gw`: list of player IDs to force into next gameweek's squad (supports per-GW like `banned_next_gw`)
  - Example: `"locked_next_gw": []`
- `keep`: list of player IDs that will not be kept throughout the player filtering process, even if they would otherwise be filtered out.
  - Example: `"keep": []`
- `price_changes`: list of `[player_ID, price_change]` pairs to simulate price changes (in £0.1m increments)
  - Example: `"price_changes": [[311, 1], [351, -1]]` simulates player 311 up £0.1m, player 351 down £0.1m
- `pick_prices`: force players at specific price points by position
  - Example: `"pick_prices": {"G": "", "D": "", "M": "8", "F": "11.5,11.5"}`

### Randomization
- `randomized`: if `true`, add random noise to expected values for varied solutions
  - Example: `"randomized": false`
- `randomization_seed`: seed for reproducible random noise (null = different seed each time)
  - Example: `"randomization_seed": null`
- `randomization_strength`: multiplier for the random noise (default 1.0)
  - Example: `"randomization_strength": 1.0`

### Chip Management
- `chip_limits`: maximum count for each chip type (note: this does not need to be edited if using `use_fh`, `use_wc` etc. )
  - Example: `"chip_limits": {"bb": 0, "wc": 0, "fh": 0, "tc": 0}`
- `no_chip_gws`: list of gameweeks where no chips can be used
  - Example: `"no_chip_gws": []`
- `allowed_chip_gws`: dictionary of chip types to lists of allowed gameweeks
  - Example: `"allowed_chip_gws": {"wc": [25, 27], "fh": [30, 31]}`
- `forced_chip_gws`: dictionary of chip types to lists of gameweeks where chip MUST be used
  - Example: `"forced_chip_gws": {"wc": [], "bb": [], "fh": [], "tc": []}`
- `preseason`: special flag for GW1 solving where team data is not important
  - Example: `"preseason": false`

### Lineup Constraints
- `no_opposing_play`: controls opposing-play logic
  - `true` = no two players can play each other in same GW
  - `false` = no constraint
  - `"penalty"` = penalize each opposing-play instance
  - Example: `"no_opposing_play": false`
- `opposing_play_group`: scope of opposing-play constraint
  - `"all"` = no opposing players at all
  - `"position"` = only offense vs defense (not M/F vs each other or D/G vs each other)
  - Example: `"opposing_play_group": "position"`
- `opposing_play_penalty`: penalty deducted per opposing-play when using `"penalty"` mode
  - Example: `"opposing_play_penalty": 0.5`
- `max_defenders_per_team`: maximum defenders + goalkeepers from single team (default 3)
  - Example: `"max_defenders_per_team": 3`
- `double_defense_pick`: forces solver to use either 0 or 2+ defenders/GKs from each team
  - Example: `"double_defense_pick": false`
- `no_gk_rotation_after`: gameweek after which to lock to single goalkeeper (no rotation)
  - Example: `"no_gk_rotation_after": null`

### Solution Variants
- `num_iterations`: number of alternative solutions to generate
  - Example: `"num_iterations": 1`
- `iteration_criteria`: rule for what makes each iteration "different"
  - Options: `"this_gw_transfer_in"`, `"this_gw_transfer_out"`, `"this_gw_transfer_in_out"`, `"chip_gws"`, `"target_gws_transfer_in"`, `"this_gw_lineup"`
  - Example: `"iteration_criteria": "this_gw_transfer_in_out"`
- `iteration_difference`: number of players that must differ (only for `"this_gw_lineup"` criteria)
  - Example: `"iteration_difference": 1`
- `iteration_target`: list of gameweeks to target for iteration changes (used with `"target_gws_transfer_in"`)
  - Example: `"iteration_target": []`

### Data Sources
- `data_weights`: weight percentage for each data source when using `"datasource": "mixed"`
  - Example: `"data_weights": {"solio": 1, "review": 1}`
- `export_data`: option to export mixed data as CSV
  - Example: `"export_data": "mixed.csv"`
- `report_decay_base`: list of decay bases to compute and report for the solve
  - Example: `"report_decay_base": [0.85, 1.0, 1.017]`

### Team Data Options
- `team_json`: supply team JSON inline (requires `"team_data": "json"`)
  - Can be run as: `uv run python solve.py --team_json '{"picks": [{...}]}'`
  - Example: `"team_json": null`
- `override_next_gw`: override the starting gameweek for planning horizon
  - Example: `"override_next_gw": null`

### Solver Behavior
- `secs`: time limit for solver in seconds
  - Example: `"secs": 600` (10 minutes)
- `gap`: relative optimality gap (0.0–1.0). Solver stops when within this gap of optimal. Set to 0 for proven optimality
  - Example: `"gap": 0` (solve to optimality)
- `delete_tmp`: if `true`, delete temporary solver files after solving
  - Example: `"delete_tmp": true`
- `single_solve`: internal flag for single solve mode
  - Example: `"single_solve": true`
- `solver`: which solver to use (e.g., `"highs"`)
  - Example: `"solver": "highs"`

### Output & Exports
- `export_image`: if `true`, generate and export lineup visualizations
  - Example: `"export_image": false`
- `solve_name`: name for the solve (used in output filenames)
  - Example: `"solve_name": "regular"`
- `print_result_table`: if `true`, print result table to console
  - Example: `"print_result_table": true`
- `print_decay_metrics`: if `true`, print decay metric analysis to console
  - Example: `"print_decay_metrics": false`
- `print_transfer_chip_summary`: if `true`, print transfer/chip summary per gameweek
  - Example: `"print_transfer_chip_summary": true`
- `print_squads`: if `true`, print full lineup and bench for each gameweek
  - Example: `"print_squads": true`
- `dataframe_format`: [tabulate](https://pypi.org/project/tabulate/) format for printing tables
  - Examples: `"plain"`, `"rounded_grid"`, `"fancy_outline"`
- `hide_transfers`: if `true`, hide transfer details in result table
  - Example: `"hide_transfers": false`
- `solutions_file`: if provided (filepath ending in `.csv`), save all solutions to this file
  - Example: `"solutions_file": ""`
- `save_squads`: if `true` and `solutions_file` is set, include lineup/bench info per gameweek
  - Example: `"save_squads": true`
- `solutions_file_player_type`: whether solutions file contains player `"id"` or `"name"`
  - Example: `"solutions_file_player_type": "name"`

### Binary Files (Advanced)
- `binary_files`: configure binary file names, weights, and team-level fixture settings
  - Example: `"binary_files": {"fplreview_binary_1.csv": 0.6, "fplreview_binary_2.csv": 0.3, "fplreview_binary_3.csv": 0.1}`
- `generate_binary_files`: if `true`, generate binary files based on fixture settings
  - Example: `"generate_binary_files": false`

---

## Complete Reference

For a complete listing of all settings and their default values, see [`comprehensive_settings.json`](comprehensive_settings.json).
| Setting | Type | User-Friendly? |
|----------------|------|----------------|
| [`allowed_chip_gws`](#chip-management) | dict | ❌ |
| [`banned`](#player-constraints) | list | ✅ |
| [`banned_next_gw`](#player-management-advanced) | list | ❌ |
| [`bench_weights`](#scoring--objective-function) | dict | ❌ |
| [`binary_files`](#binary-files-advanced) | dict | ❌ |
| [`booked_transfers`](#transfer--hit-management) | list | ❌ |
| [`chip_limits`](#chip-management) | dict | ❌ |
| [`data_weights`](#data-sources) | dict | ❌ |
| [`datasource`](#data-source--team) | string | ✅ |
| [`dataframe_format`](#output--exports) | string | ❌ |
| [`decay_base`](#decay--valuation) | float | ✅ |
| [`delete_tmp`](#solver-behavior) | bool | ❌ |
| [`double_defense_pick`](#lineup-constraints) | bool | ❌ |
| [`ev_per_price_cutoff`](#player-pool-filtering) | int | ✅ |
| [`export_data`](#data-sources) | string | ❌ |
| [`export_image`](#output--exports) | bool | ❌ |
| [`force_ft_state_lb`](#transfer--hit-management) | list | ❌ |
| [`force_ft_state_ub`](#transfer--hit-management) | list | ❌ |
| [`forced_chip_gws`](#chip-management) | dict | ❌ |
| [`ft_use_penalty`](#scoring--objective-function) | float | ❌ |
| [`ft_value`](#scoring--objective-function) | float | ❌ |
| [`ft_value_list`](#decay--valuation) | dict | ✅ |
| [`future_transfer_limit`](#transfer--hit-management) | int | ❌ |
| [`gap`](#solver-behavior) | float | ❌ |
| [`generate_binary_files`](#binary-files-advanced) | bool | ❌ |
| [`hide_transfers`](#output--exports) | bool | ❌ |
| [`hit_cost`](#transfer--hit-management) | int | ❌ |
| [`hit_limit`](#transfer--hit-management) | int | ❌ |
| [`horizon`](#planning-horizon) | int | ✅ |
| [`itb_loss_per_transfer`](#scoring--objective-function) | float | ❌ |
| [`itb_value`](#scoring--objective-function) | float | ❌ |
| [`iteration_criteria`](#solution-variants) | string | ❌ |
| [`iteration_difference`](#solution-variants) | int | ❌ |
| [`iteration_target`](#solution-variants) | list | ❌ |
| [`keep`](#player-management-advanced) | list | ❌ |
| [`keep_top_ev_percent`](#player-pool-filtering) | int | ✅ |
| [`locked`](#player-constraints) | list | ✅ |
| [`locked_next_gw`](#player-management-advanced) | list | ❌ |
| [`max_defenders_per_team`](#lineup-constraints) | int | ❌ |
| [`no_chip_gws`](#chip-management) | list | ❌ |
| [`no_future_transfer`](#transfer--hit-management) | bool | ❌ |
| [`no_gk_rotation_after`](#lineup-constraints) | int | ❌ |
| [`no_opposing_play`](#lineup-constraints) | bool/str | ❌ |
| [`no_transfer_by_position`](#transfer--hit-management) | list | ❌ |
| [`no_transfer_gws`](#transfer--hit-management) | list | ❌ |
| [`no_transfer_last_gws`](#transfer-constraints) | int | ✅ |
| [`no_trs_except_wc`](#transfer--hit-management) | bool | ❌ |
| [`num_iterations`](#solution-variants) | int | ❌ |
| [`num_transfers`](#transfer--hit-management) | int | ❌ |
| [`only_booked_transfers`](#transfer--hit-management) | bool | ❌ |
| [`opposing_play_group`](#lineup-constraints) | string | ❌ |
| [`opposing_play_penalty`](#lineup-constraints) | float | ❌ |
| [`override_next_gw`](#team-data-options) | int | ❌ |
| [`pick_prices`](#player-management-advanced) | dict | ❌ |
| [`preseason`](#chip-management) | bool | ❌ |
| [`price_changes`](#player-management-advanced) | list | ❌ |
| [`print_decay_metrics`](#output--exports) | bool | ❌ |
| [`print_result_table`](#output--exports) | bool | ❌ |
| [`print_squads`](#output--exports) | bool | ❌ |
| [`print_transfer_chip_summary`](#output--exports) | bool | ❌ |
| [`randomization_seed`](#randomization) | int/null | ❌ |
| [`randomization_strength`](#randomization) | float | ❌ |
| [`randomized`](#randomization) | bool | ❌ |
| [`report_decay_base`](#data-sources) | list | ❌ |
| [`secs`](#solver-behavior) | int | ❌ |
| [`single_solve`](#solver-behavior) | bool | ❌ |
| [`solver`](#solver-behavior) | string | ❌ |
| [`solutions_file`](#output--exports) | string | ❌ |
| [`solutions_file_player_type`](#output--exports) | string | ❌ |
| [`solve_name`](#output--exports) | string | ❌ |
| [`team_data`](#data-source--team) | string | ✅ |
| [`team_id`](#data-source--team) | int | ✅ |
| [`team_json`](#team-data-options) | object | ❌ |
| [`transfer_itb_buffer`](#transfer--hit-management) | float | ❌ |
| [`use_bb`](#chips) | list | ✅ |
| [`use_fh`](#chips) | list | ✅ |
| [`use_tc`](#chips) | list | ✅ |
| [`use_wc`](#chips) | list | ✅ |
| [`vcap_weight`](#scoring--objective-function) | float | ❌ |
| [`verbose`](#output) | bool | ✅ |
| [`xmin_lb`](#player-pool-filtering) | int | ✅ |
