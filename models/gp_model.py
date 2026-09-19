import sqlite3
import pandas as pd
import numpy as np
from scipy.spatial.distance import cdist
from scipy.linalg import cho_solve, cho_factor

def rbf_kernel(dists, length_scale, sigma):
    return sigma**2 * np.exp(-0.5 * (dists / length_scale)**2)


def generate_std_for_nan(t_offset,backoff=1):
    b = np.float64(0.9695819463339511) 
    a = np.float64(0.009348373050687439)
    return a*t_offset+ b + backoff


def compute_daily_temp(time,db_path='mountains.db',length_scale=10,sigma=1,batch_size=500):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    
    # 1. Load measured forecasts for this time
    query_measured = """
    SELECT mf.mountain_id, mf.air_temperature, mf.air_temperature_percentile_10, mf.air_temperature_percentile_90, m.lat, m.lon, mf.time_offset, mf.measured_at
    FROM measured_forecast mf
    JOIN mountains m ON mf.mountain_id = m.id
    WHERE mf.time = ?
    """
    df_measured = pd.read_sql_query(query_measured, conn, params=(time,))
    if df_measured.empty:
        print(f"No measured forecasts available for time {time}")
        conn.close()
        return

    # compute forcasts std
    df_measured["temp_std"] = (df_measured['air_temperature_percentile_90']-df_measured['air_temperature_percentile_10'])/2.563
    # Fill nan values for forcasts without percentiles  based on fit function
    df_measured["temp_std"] = df_measured["temp_std"].fillna(generate_std_for_nan(df_measured["time_offset"]))

    # Remove oldes forcast if multible forcasts exist for a certain time
    df_measured = df_measured.sort_values("measured_at", ascending=False)
    df_measured = df_measured.drop_duplicates(subset='mountain_id', keep='first')

    # 2. Load all mountain coordinates
    df_all = pd.read_sql_query("SELECT id AS mountain_id, lat, lon FROM mountains", conn)
    coords_all = df_all[['lon', 'lat']].to_numpy()

    # 3. Map mountain_id to row index
    id_to_idx = {mid: i for i, mid in enumerate(df_all['mountain_id'].to_numpy())}
    idx_m = np.array([id_to_idx[mid] for mid in df_measured['mountain_id']])
    coords_m = coords_all[idx_m]
    y_m = df_measured['air_temperature'].to_numpy()

    measured_mask = np.zeros( len(coords_all), dtype=bool ) 
    measured_mask[idx_m] = True
    
    
    # K_mm and K_*m
    K_mm = rbf_kernel(
        cdist(coords_m, coords_m), 
        length_scale=length_scale, 
        sigma=sigma
        )
    K_all_m = rbf_kernel(
        cdist(coords_all, coords_m), 
        length_scale=length_scale, 
        sigma=sigma
        )
    
    # Cholesky factor
    #noise_var = (df_measured['time_offset'].to_numpy() * timescale)**2
    noise_var = (df_measured['temp_std'].to_numpy())**2
    L = cho_factor(K_mm + np.diag(noise_var) + 1e-6 * np.eye(len(idx_m)))

    # Predict temperatures for all mountains
    temp_pred = K_all_m @ cho_solve(L, y_m)

    # Variance prediction
    v = cho_solve(L, K_all_m.T)
    K_all_all_diag = np.full(len(coords_all), sigma**2)
    var_pred = K_all_all_diag - np.sum(K_all_m * v.T, axis=1)
    var_pred = np.maximum(var_pred, 0)
    std_pred = np.sqrt(var_pred)

    # Compute IG
    information_gain = compute_information_gain(
        coords_all,
        coords_m,
        idx_m,
        measured_mask,
        K_all_m,
        L,
        noise_var,
        length_scale,
        sigma,
        new_observation_noise=0.1,
        batch_size=batch_size
    )
    
    # 4. Prepare DataFrame to insert
    df_estimates = pd.DataFrame({
        'mountain_id': df_all['mountain_id'],
        'time': time,
        'air_temperature': temp_pred,
        'uncertainty': std_pred,
        'information_gain': information_gain
    })

    # 5. Insert into estimated_forecast table
    df_estimates.to_sql('estimated_forecast', conn, if_exists='append', index=False)
    conn.close()
    print(f"Inserted {len(df_estimates)} estimates for time {time}")
    return set(df_measured["mountain_id"])



def compute_information_gain(
        coords_all,
        coords_m,
        idx_m,
        measured_mask,
        K_all_m,
        L,
        old_noise_var,
        length_scale,
        sigma,
        new_observation_noise=0.1,
        batch_size=500
    ):
    """
    Calculate the expected reduction in total posterior variance
    from querying each mountain.
    For an unobserved mountain: add a fresh observation
    For an already observed mountain: replace the old observation with a fresh one

    Returns: IG value for every mountain.
    """

    n_mountains = len(coords_all)
    information_gain = np.zeros(n_mountains,dtype=float)
    candidate_idx = np.arange(n_mountains)
    new_noise_var = new_observation_noise ** 2

    for start in range(0,len(candidate_idx),batch_size):
        batch_idx = candidate_idx[start:start + batch_size]
        coords_batch = coords_all[batch_idx]

        # Covariance: all mountains vs candidates
        K_all_C = rbf_kernel(cdist(coords_all,coords_batch),length_scale,sigma)

        # Covariance: measured mountains vs candidates
        K_m_C = rbf_kernel(cdist(coords_m,coords_batch),length_scale,sigma)

        # A^-1 K_m_C
        V = cho_solve(L,K_m_C)

        
        # Posterior covariance:
        # Sigma[:, candidate]
        posterior_cov = (K_all_C- K_all_m @ V)
        
        # Calculate IG for each candidate
        for k, mountain_idx in enumerate(batch_idx):
            covariance_column = (posterior_cov[:, k])
            posterior_variance = max(covariance_column[mountain_idx],0.0)

            if not measured_mask[mountain_idx]:
                # New measurement
                information_gain[mountain_idx] = (
                    np.sum(covariance_column ** 2)/(posterior_variance+ new_noise_var)
                )

            else:
                # Refresh a measuremnt
                measured_position = np.where(idx_m == mountain_idx)[0][0]
                old_noise = (old_noise_var[measured_position])

                # If the old measuremnt is already very precise,
                # refreshing it has little value.
                if old_noise <= posterior_variance + 1e-12:
                    information_gain[mountain_idx] = 0.0
                    continue

                # Remove old measuremnt
                removal_denominator = old_noise- posterior_variance
                covariance_without_old = covariance_column * old_noise / removal_denominator
                variance_without_old = posterior_variance * old_noise / removal_denominator
                
                # Add fresh measuremnt
                addition_denominator = variance_without_old + new_noise_var
                reduction_from_new = np.sum(covariance_without_old ** 2) / addition_denominator
                
                # Benefit of replacing old measuremnt
                loss_from_removing_old = np.sum(covariance_column ** 2) / removal_denominator
                

                information_gain[mountain_idx] = reduction_from_new - loss_from_removing_old
                

    return np.maximum(information_gain, 0.0)