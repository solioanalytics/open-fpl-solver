import pandas as pd

from paths import DATA_DIR


def generate_binary_files(file_path, fixtures_json):
    # Iterate through each binary file entry in config
    for file_name, fixtures in fixtures_json.items():
        # Load original fixture CSV file
        df = pd.read_csv(file_path)

        for team, binary_fix in fixtures.items():
            # Apply changes only to rows where the Team column matches the specified team
            team_mask = df["Team"] == team

            for orig_gw, new_gw in binary_fix.items():
                new_gw_pts_col = f"{new_gw}_Pts"
                new_gw_xmins_col = f"{new_gw}_xMins"
                orig_gw_pts_col = f"{orig_gw}_Pts"
                orig_gw_xmins_col = f"{orig_gw}_xMins"

                if not new_gw:
                    df.loc[team_mask, orig_gw_pts_col] = 0
                    df.loc[team_mask, orig_gw_xmins_col] = 0

                # Ensure relevant columns exist in the dataframe
                if all(col in df.columns for col in [new_gw_pts_col, new_gw_xmins_col, orig_gw_pts_col, orig_gw_xmins_col]):
                    # Convert columns to numeric values for the rows we are updating
                    df.loc[team_mask, new_gw_pts_col] = pd.to_numeric(df.loc[team_mask, new_gw_pts_col], errors="coerce")
                    df.loc[team_mask, new_gw_xmins_col] = pd.to_numeric(df.loc[team_mask, new_gw_xmins_col], errors="coerce")
                    df.loc[team_mask, orig_gw_pts_col] = pd.to_numeric(df.loc[team_mask, orig_gw_pts_col], errors="coerce")
                    df.loc[team_mask, orig_gw_xmins_col] = pd.to_numeric(df.loc[team_mask, orig_gw_xmins_col], errors="coerce")

                    # Update target GW xPts by adding xPts from original GW
                    df.loc[team_mask, new_gw_pts_col] += df.loc[team_mask, orig_gw_pts_col]

                    # Use target GW xMins
                    df.loc[team_mask, new_gw_xmins_col] = df.loc[team_mask, new_gw_xmins_col]

                    # Zero out key_value_Pts and key_value_xMins
                    df.loc[team_mask, [orig_gw_pts_col, orig_gw_xmins_col]] = 0

        # Save the updated CSV file
        df.to_csv(DATA_DIR / file_name, index=False)
        print(f"Generated: {file_name}")
