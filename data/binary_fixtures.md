## Binary Files

This file contains instructions for generating binary files for when there are fixture uncertainties, for use with `simulations.py`.

### Instructions

1. Set the fixtures in your data source so there are no blank or double gameweeks, i.e. each team should have the fixtures as originally scheduled in their respective GW. Download that CSV and save it as `data/original.csv`.

2. Configure the settings in your `data/user_settings.json` file.

    **(a) binary_fixture_settings** - Create the settings that describe which fixtures are being moved in each binary file. For each team, the dictionary contains `{a:b}` pairs, where `a` represents the gameweek the expected points will be taken from, and `b` represents the gameweek where the expected points will be put into. In the example configuration provided below and looking at `binary1.csv`, expected points will be moved from GW33 to GW34 for Bournemounth, Man Utd, Man City, and Aston Villa, and from GW36 to GW34 for Newcastle and Ipswich. Note that the team names here should match with the team names in your data source.

    **(b) binary_file_weights** - Set the weighting for each possible binary file. In the example below, 60% of simulations will use `binary1.csv`, 30% will use `binary2.csv`, and 10% will use `binary3.csv`.

    **(c) generate_binary_files** - Set this to `true` if you want to generate the binary files. This is only a setting because if you run multiple sets of simulations, there is sometimes no need to regenerate the files, so this setting can just be set to `false`.

3. Run `simulations.py` and when it asks whether you want to use binaries, respond with `y`.

### Example JSON Settings

```json
{
  "generate_binary_files": true,
  "binary_file_weights": {
    "binary1.csv": 0.6,
    "binary2.csv": 0.3,
    "binary3.csv": 0.1
  },
  "binary_fixture_settings": {
    "binary1.csv": {
      "Bournemouth": { "33": "34" },
      "Man Utd": { "33": "34" },
      "Man City": { "33": "34" },
      "Aston Villa": { "33": "34" },
      "Newcastle": { "36": "34" },
      "Ipswich": { "36": "34" }
    },
    "binary2.csv": {
      "Bournemouth": { "33": "34", "36": "37" },
      "Man Utd": { "33": "34" },
      "Man City": { "33": "34", "36": "37" },
      "Aston Villa": { "33": "34" },
      "Newcastle": { "36": "34" },
      "Ipswich": { "36": "34" }
    },
    "binary3.csv": {
      "Man City": { "33": "34" },
      "Aston Villa": { "33": "34" },
      "Newcastle": { "36": "34" },
      "Ipswich": { "36": "34" },
      "Arsenal": { "36": "34" },
      "Crystal Palace": { "36": "34" }
    }
  }
}
```
