import json
import random
import string
import time
from itertools import product
from pathlib import Path

import requests

from paths import DATA_DIR

# Cache configuration
CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_FILE = CACHE_DIR / "http_cache.json"
CACHE_EXPIRATION = 300


def load_settings():
    with open(DATA_DIR / "comprehensive_settings.json") as f:
        options = json.load(f)
    with open(DATA_DIR / "user_settings.json") as f:
        options = {**options, **json.load(f)}

    return options


def get_random_id(n):
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))


def xmin_to_prob(xmin, sub_on=0.5, sub_off=0.3):
    start = min(max((xmin - 25 * sub_on) / (90 * (1 - sub_off) + 65 * sub_off - 25 * sub_on), 0.001), 0.999)
    return start + (1 - start) * sub_on


def get_dict_combinations(my_dict):
    keys = my_dict.keys()
    for key in keys:
        if my_dict[key] is None or len(my_dict[key]) == 0:
            my_dict[key] = [None]
    all_combs = [dict(zip(my_dict.keys(), values, strict=False)) for values in product(*my_dict.values())]
    feasible_combs = []
    for comb in all_combs:
        c_values = [i for i in comb.values() if i is not None]
        if len(c_values) == len(set(c_values)):
            feasible_combs.append({k: [v] for k, v in comb.items() if v is not None})
        # else we have a duplicate
    return feasible_combs


def load_config_files(config_paths):
    """
    Load and merge multiple configuration files.
    Files are merged in order, with later files overriding earlier ones.
    """
    merged_config = {}
    if not config_paths:
        return merged_config

    paths = config_paths.split(";")
    for path in paths:
        stripped_path = path.strip()
        if not path:
            continue
        try:
            with open(stripped_path) as f:
                config = json.load(f)
                merged_config.update(config)
        except FileNotFoundError:
            print(f"Warning: Configuration file {stripped_path} not found")
        except json.JSONDecodeError:
            print(f"Warning: Configuration file {stripped_path} is not valid JSON")

    return merged_config


def cached_request(url):
    """
    Fetch data from URL with caching support.
    Returns cached data if available and not expired (< 24 hours old).
    Otherwise fetches fresh data, updates cache, and returns the data.

    Args:
        url: The URL to fetch data from

    Returns:
        dict: JSON response from the URL
    """
    # Create cache directory if it doesn't exist
    CACHE_DIR.mkdir(exist_ok=True)

    # Load existing cache
    cache = {}
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE) as f:
                cache = json.load(f)
        except (json.JSONDecodeError, IOError):
            # If cache is corrupted, start fresh
            cache = {}

    # Check if URL is in cache and not expired
    current_time = time.time()
    if url in cache:
        cached_entry = cache[url]
        timestamp = cached_entry.get("timestamp", 0)
        if current_time - timestamp < CACHE_EXPIRATION:
            # Cache is still valid
            return cached_entry["data"]

    # Cache miss or expired - fetch fresh data
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()

        # Update cache
        cache[url] = {
            "data": data,
            "timestamp": current_time
        }

        # Save cache to file
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)

        return data

    except requests.RequestException as e:
        # If network request fails and we have expired cache, return it anyway
        if url in cache:
            print(f"Warning: Failed to fetch {url}, using expired cache. Error: {e}")
            return cache[url]["data"]
        raise
