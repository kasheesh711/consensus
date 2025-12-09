import streamlit as st
import pandas as pd
import duckdb
import plotly.graph_objects as go
import plotly.express as px
import os

# =============================================================================
# CONFIGURATION
# =============================================================================
st.set_page_config(page_title="Inventory Planning Dashboard (DuckDB Powered)", layout="wide")

# Default Local Files
FILE_WATERFALL_PQ = "inventory_waterfall_deltas.parquet"
FILE_DAILY_PQ = "inventory_demand_daily.parquet"
FILE_VARIANCE_PQ = "demand_forecast_variance.parquet"

# =============================================================================
# DATA LOADING & DB CONNECTION
# =============================================================================

@st.cache_resource
def get_db_connection():
    """
    Creates an in-memory DuckDB connection. 
    We cache resource so it persists across reruns.
    """
    con = duckdb.connect(database=':memory:')
    return con

con = get_db_connection()

st.sidebar.header("Data Source")

# Uploaders
uploaded_wf = st.sidebar.file_uploader("Upload Waterfall Data (.parquet)", type="parquet")
uploaded_daily = st.sidebar.file_uploader("Upload Daily Data (.parquet)", type="parquet")
uploaded_var = st.sidebar.file_uploader("Upload Variance Data (.parquet)", type="parquet")

def register_table(con, table_name, local_path, uploaded_file):
    """
    Registers a table in DuckDB from either a local parquet file or an uploaded file.
    Returns True if successful, False otherwise.
    """
    try:
        if uploaded_file is not None:
            # For uploaded files, we might need to save them temp or read directly
            # DuckDB can read from file-like objects in newer versions, 
            # but usually it expects a path.
            # Workaround: Streamlit stores uploaded files in RAM.
            # We can write to a temp file or try to load bytes.
            # For simplicity in this local context, let's write to a temp file.
            
            with open(f"temp_{table_name}.parquet", "wb") as f:
                f.write(uploaded_file.getbuffer())
            
            query = f"CREATE OR REPLACE VIEW {table_name} AS SELECT * FROM 'temp_{table_name}.parquet'"
            con.execute(query)
            return True
            
        elif os.path.exists(local_path):
            query = f"CREATE OR REPLACE VIEW {table_name} AS SELECT * FROM '{local_path}'"
            con.execute(query)
            return True
            
        else:
            return False
    except Exception as e:
        st.error(f"Error loading {table_name}: {e}")
        return False

# Register tables
has_wf = register_table(con, 'waterfall', FILE_WATERFALL_PQ, uploaded_wf)
has_daily = register_table(con, 'daily', FILE_DAILY_PQ, uploaded_daily)
has_var = register_table(con, 'variance', FILE_VARIANCE_PQ, uploaded_var)

if not (has_wf and has_daily):
    st.warning("Please upload the required Parquet files or ensure they exist locally.")
    st.info("Required: Waterfall Data and Daily Data.")
    st.stop()
    
# Check row counts efficiently
row_count = con.execute("SELECT COUNT(*) FROM waterfall").fetchone()[0]
st.sidebar.info(f"Loaded {row_count:,} waterfall rows (DuckDB).")

# =============================================================================
# SIDEBAR FILTERS (SQL POWERED)
# =============================================================================
st.sidebar.header("Global Filters")

# Org Filter
# Efficient DISTINCT query
all_orgs_res = con.execute("SELECT DISTINCT \"Inv Org\" FROM waterfall ORDER BY 1").fetchall()
all_orgs = [row[0] for row in all_orgs_res]

selected_org = st.sidebar.selectbox("Select Inv Org", all_orgs)

# Item Filter (Contextual)
# SQL WHERE
avail_items_res = con.execute(f"SELECT DISTINCT \"Item Code\" FROM waterfall WHERE \"Inv Org\" = ?", [selected_org]).fetchall()
avail_items = sorted([row[0] for row in avail_items_res])

if not avail_items:
    st.sidebar.warning("No items found for this Org.")
    st.stop()
    
selected_item = st.sidebar.selectbox("Select Item Code", avail_items)

# =============================================================================
# TABS
# =============================================================================
tab1, tab2, tab3 = st.tabs(["Waterfall Analysis", "Supply vs Demand", "Forecast Variance"])

# --- TAB 1: WATERFALL ---
with tab1:
    st.subheader(f"Inventory Forecast Waterfall: {selected_item} @ {selected_org}")
    
    # 1. Get Available Dates
    dates_res = con.execute("""
        SELECT DISTINCT Date 
        FROM waterfall 
        WHERE "Item Code" = ? AND "Inv Org" = ? 
        ORDER BY Date
    """, [selected_item, selected_org]).fetchall()
    
    avail_dates = [pd.to_datetime(r[0]) for r in dates_res]
    
    if not avail_dates:
        st.warning("No waterfall data selection.")
    else:
        target_date = st.selectbox("Select Forecast Target Date", avail_dates, index=0)
        
        # 2. Get Data for Plot
        # Fetch only relevant rows, sort in SQL
        wf_df = con.execute("""
            SELECT "Snapshot Date", "Previous Snapshot Date", "Tot.Inventory_daily", 
                   "Tot.Inventory_previous", "Delta_Inventory"
            FROM waterfall
            WHERE "Item Code" = ? 
              AND "Inv Org" = ? 
              AND Date = ?
            ORDER BY "Snapshot Date"
        """, [selected_item, selected_org, target_date]).fetchdf()
        
        if wf_df.empty:
            st.warning("No data found.")
        else:
            # Ensure proper types
            wf_df['Snapshot Date'] = pd.to_datetime(wf_df['Snapshot Date'])
            wf_df['Previous Snapshot Date'] = pd.to_datetime(wf_df['Previous Snapshot Date'])
            
            # Logic: Base + Deltas
            first_row = wf_df.iloc[0]
            base_inv = first_row['Tot.Inventory_previous']
            base_date = first_row['Previous Snapshot Date']
            
            x_vals = [f"Base ({base_date.strftime('%m-%d')})"] + \
                     wf_df['Snapshot Date'].dt.strftime('%m-%d').tolist() + ["Final"]
            
            y_vals = [base_inv] + wf_df['Delta_Inventory'].tolist() + [None]
            measure = ["absolute"] + ["relative"] * len(wf_df) + ["total"]
            
            fig = go.Figure(go.Waterfall(
                x=x_vals, measure=measure, y=y_vals, base=0,
                decreasing={"marker":{"color":"#EF553B"}},
                increasing={"marker":{"color":"#00CC96"}},
                totals={"marker":{"color":"#636EFA"}}
            ))
            fig.update_layout(
                title=f"Evolution of Forecast for {target_date.date()}", 
                waterfallgap=0.3,
                xaxis=dict(type='category')
            )
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(wf_df)

# --- TAB 2: SUPPLY vs DEMAND ---
with tab2:
    st.subheader("Supply & Demand Timeline")
    
    # 1. Get Snaps from DAILY table
    snaps_res = con.execute("""
        SELECT DISTINCT "Snapshot Date" 
        FROM daily 
        WHERE "Item Code" = ? AND "Inv Org" = ?
        ORDER BY 1
    """, [selected_item, selected_org]).fetchall()
    
    all_snaps = [pd.to_datetime(r[0]) for r in snaps_res]
    
    if not all_snaps:
        st.write("No daily data.")
    else:
        selected_snap = st.selectbox("Select Forecast Snapshot", all_snaps, index=len(all_snaps)-1)
        
        ts_df = con.execute("""
            SELECT Date, "Tot.Inventory_daily", "Indep.Req_daily", "Net_Inventory_vs_Demand"
            FROM daily
            WHERE "Item Code" = ? 
              AND "Inv Org" = ?
              AND "Snapshot Date" = ?
            ORDER BY Date
        """, [selected_item, selected_org, selected_snap]).fetchdf()
        
        ts_df['Date'] = pd.to_datetime(ts_df['Date'])
        
        fig2 = px.line(ts_df, x='Date', y=['Tot.Inventory_daily', 'Indep.Req_daily'],
                       labels={'value': 'Quantity', 'variable': 'Metric'},
                       title=f"Plan as of {selected_snap.date()}")
        st.plotly_chart(fig2, use_container_width=True)
        
        total_short = ts_df[ts_df['Net_Inventory_vs_Demand'] < 0]['Net_Inventory_vs_Demand'].sum()
        st.metric("Total Cumulative Shortage", f"{total_short:,.0f}")

# --- TAB 3: VARIANCE ---
with tab3:
    st.subheader("Demand Forecast Variance")
    
    if has_var:
        var_df = con.execute("""
            SELECT * 
            FROM variance
            WHERE "Item Code" = ? AND "Inv Org" = ?
            ORDER BY Date
        """, [selected_item, selected_org]).fetchdf()
        
        st.dataframe(var_df)
        
        if not var_df.empty:
            var_df['Date'] = pd.to_datetime(var_df['Date'])
            fig3 = px.scatter(var_df, x='Date', y='cv_demand_forecast', 
                              size='mean_demand_forecast',
                              title="Demand Volatility (CV) over Time")
            st.plotly_chart(fig3, use_container_width=True)
    else:
        st.warning("Variance data not uploaded.")
