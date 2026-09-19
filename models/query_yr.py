import time
import random 
import os
import json
from datetime import datetime
import pandas as pd
import requests
from tqdm import tqdm

CACHE_DIR = "./yr_cache/"
ERROR_CACHE_DIR = "./error_cache/"

def truncate_coord(coord, decimals=4):
    return round(coord, decimals)

def fetch_yr_data(lat, lon, altitude, idx, cache=True):
     # Truncate coordinates to max 4 decimals
    lat = truncate_coord(lat)
    lon = truncate_coord(lon)
    
    # Construct URL
    url = f"https://api.met.no/weatherapi/locationforecast/2.0/complete?lat={lat}&lon={lon}&altitude={altitude}"
    
    # File path for cached response
    #cache_file = os.path.join(CACHE_DIR, f"forecast_{lat:.4f}_{lon:.4f}.json")
    cache_file = os.path.join(CACHE_DIR, f"forecast_{idx}.json")

    headers = {
        "User-Agent": "SkiApp/1.0 paul.jonathan.groening@gmail.com",  # REQUIRED by Yr
        "Accept": "application/json",
    }
    
    # If caching, add If-Modified-Since header
    # if cache and os.path.exists(cache_file):
     #   last_modified = datetime.utcfromtimestamp(os.path.getmtime(cache_file))
      #  headers["If-Modified-Since"] = last_modified.strftime("%a, %d %b %Y %H:%M:%S GMT")
    if cache and os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                old_cache = json.load(f)
            last_modified = old_cache.get("meta", {}).get("headers", {}).get("Last-Modified")
            if last_modified:
                headers["If-Modified-Since"] = last_modified
        except Exception:
            pass
    
    # Randomize request slightly to spread traffic evenly
    time.sleep(random.uniform(0.5, 2.0))  # wait 0.5–2 seconds before request
    
    try:
        response = requests.get(url, headers=headers)
        # Handle throttling
        if response.status_code == 429:
            print("Throttled by Yr API. Backing off...")
            time.sleep(10 + random.uniform(0, 5))  # wait 10–15 seconds
            return None
        
        # If not modified, read from cache
        if response.status_code == 304 and os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                cache_obj = json.load(f)
        
        # On 200 or 203, save response to cache
        elif response.status_code in [200, 203]:
            try:
                data = response.json()
            except Exception:
                data = response.text

            cache_obj = {
                "meta": {
                    "requested_at": datetime.utcnow().isoformat() + "Z",
                    "url": url,
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                },
                "content": data,
            }

            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cache_obj, f, ensure_ascii=False, indent=2)

        else:
            # Other codes
            print(f"Unexpected status code: {response.status_code}")
            try:
                err_content = response.json()
            except Exception:
                err_content = response.text
            error_file = os.path.join(
                ERROR_CACHE_DIR,
                f"error_{response.status_code}_{idx}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
            )
            error_obj = {
                "meta": {
                    "requested_at": datetime.utcnow().isoformat() + "Z",
                    "url": url,
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                },
                "content": err_content,
            }
            with open(error_file, "w", encoding="utf-8") as f:
                json.dump(error_obj, f, ensure_ascii=False, indent=2)
            print(f"Saved error response to {error_file}")
            return None
        return cache_obj
    
    except Exception as e:
        print(f"Error fetching forecast: {e}")
        return None

def parse_yr_dict_to_sql(yr_data, idx, conn,is_val=False):
        properties = yr_data["content"]['properties']
        timeseries = properties['timeseries']
        measured_at = properties["meta"]["updated_at"]
        rows = []
        for entry in timeseries:
            time_val = entry.get("time")
            details = entry.get("data", {}).get("instant", {}).get("details", {})
            row = {
                        "mountain_id": idx,
                        "time": time_val,
                        "measured_at": measured_at,
                        "is_val": is_val
                    }

            row.update(details)
            try:
                row["time_offset"] = (
                    pd.to_datetime(time_val) - pd.to_datetime(measured_at)
                ).total_seconds()
            except Exception:
                row["time_offset"] = None

            rows.append(row)

        if not rows:
            return
        
        df = pd.DataFrame(rows)

        #df.to_sql("measured_forecast", conn, if_exists="append", index=False)

        cursor = conn.cursor()
        for col in df.select_dtypes(include=['int64', 'float64']).columns:
            df[col] = df[col].apply(lambda x: int(x) if pd.api.types.is_integer_dtype(df[col]) else float(x))

        cols = ', '.join(df.columns)
        placeholders = ', '.join(['?'] * len(df.columns))
        data_to_insert = [tuple(x) for x in df.to_numpy()]
        query = f"INSERT OR REPLACE INTO measured_forecast ({cols}) VALUES ({placeholders})"
        cursor.executemany(query, data_to_insert)
        conn.commit()
        print(f"Upserted {len(df)} rows for mountain {idx}")



def get_yr_forecast(lat, lon, altitude, idx, conn, cache=True, is_val=False):
    """
    Request forecast from Yr API with rules compliance.
    lat, lon: float
    cache: whether to use local cache and If-Modified-Since
    """
    
    yr_data_json = fetch_yr_data(lat, lon, altitude, idx, cache=cache)
    if not yr_data_json:
        print(f"No data for mountain {idx}")
        return
    parse_yr_dict_to_sql(yr_data_json, idx, conn,is_val)






