import time
from config import WEATHER_ALTITUDE
from tqdm import tqdm
from models.query_yr import get_yr_forecast
from models.gp_model import compute_daily_temp
import pandas as pd
from database import query_dataframe,get_connection
from datetime import datetime, timezone

def query_peaks(conn):
    mountains_df = query_dataframe(
        """SELECT 
        id,
        name,
        elevation,
        vertical_separation,
        lat,
        lon,
        difficulty,
        n_tourreports,
        kmeans_label
        FROM mountains;""", conn) 
    return mountains_df

def query_forecasts(conn, date=None):
    # TODO: change to just query forcasts for future times
    if date:
        query_dataframe(
        f"SELECT * FROM measured_forecast WHERE time >= '{date}';",
        conn
        )

    return query_dataframe(
        "SELECT * FROM measured_forecast;",
        conn
        ) 

def query_forecasts_estimates(conn,date=None):
    # TODO: change to just query forcasts for future times
    if date:
        query_dataframe(
        f"SELECT * FROM estimated_forecast WHERE time >= '{date}';",
        conn
        )
    return query_dataframe(
        "SELECT * FROM estimated_forecast;",
        conn
        ) 



def pipeline(test=False):
    sample_per_cluster = 2
    val_sample_per_cluster = 1
    # Open connection
    conn = get_connection()
    # Sample 200 mountain tops
    current_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    estimated_forecast = query_forecasts_estimates(conn, current_time)
    mountains_df = query_peaks(conn)
    if len(estimated_forecast) > 0:
        estimated_forecast = estimated_forecast.merge(
            mountains_df[['id', 'lat', 'lon','kmeans_label']].rename(columns={'id': 't_id'}),
            left_on='mountain_id',
            right_on='t_id',
            how='left'
        ).drop(columns='t_id')
        estimated_forecast = estimated_forecast.sort_values("id").drop_duplicates(["mountain_id", "time"], keep="last")
        gain = estimated_forecast.groupby("mountain_id")["information_gain"].sum().rename("gain")
        #sampled_mountains_df = estimated_forecast.groupby("kmeans_label").sample(n=sample_per_cluster,weights="information_gain")
        sampled_mountains_df = mountains_df.merge(gain, left_on="id", right_index=True, how="left").groupby("kmeans_label").sample(n=sample_per_cluster,weights="gain")
        
    else:
        sampled_mountains_df = mountains_df.groupby("kmeans_label").sample(n=2)
        


    for index, row in tqdm(sampled_mountains_df.iterrows()):
        get_yr_forecast(row["lat"], row["lon"],WEATHER_ALTITUDE, row["id"], conn,is_val=False)
        time.sleep(1)

    
    forcasts_df = query_forecasts(conn, current_time)
    measured_mountain_ids = set()
    for t in set(forcasts_df["time"]): 
        measured_mountain_ids = measured_mountain_ids.union(
            compute_daily_temp(t,db_path='mountains.db',length_scale=10,sigma=1,batch_size=500)
            )

    val_mountains_df = mountains_df[~mountains_df["id"].isin(measured_mountain_ids)].groupby("kmeans_label").sample(n=val_sample_per_cluster)
    for index, row in tqdm(val_mountains_df.iterrows()):
        get_yr_forecast(row["lat"], row["lon"],WEATHER_ALTITUDE, row["id"], conn,is_val=True)
        time.sleep(1)
    conn.close()
    return 


# load mountain database
# sample 100 random mountains from the dataset based on cluster and uncertanty
# Query yr for these peaks
# Overwrite or 
