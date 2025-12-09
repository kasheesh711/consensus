import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

# =============================================================================
# CONFIGURATION
# =============================================================================
st.set_page_config(page_title="Inventory Planning Dashboard", layout="wide")

FILE_WATERFALL = "inventory_waterfall_deltas.csv"
FILE_DAILY = "inventory_demand_daily.csv"
FILE_VARIANCE = "demand_forecast_variance.csv"

# =============================================================================
# DATA LOADING
# =============================================================================
@st.cache_data
def load_data():
    try:
        df_wf = pd.read_csv(FILE_WATERFALL)
        df_daily = pd.read_csv(FILE_DAILY)
        df_var = pd.read_csv(FILE_VARIANCE)
        
        # Parse dates
        df_wf['Snapshot Date'] = pd.to_datetime(df_wf['Snapshot Date'])
        df_wf['Previous Snapshot Date'] = pd.to_datetime(df_wf['Previous Snapshot Date'])
        df_wf['Date'] = pd.to_datetime(df_wf['Date'])
        
        df_daily['Date'] = pd.to_datetime(df_daily['Date'])
        
        return df_wf, df_daily, df_var
    except FileNotFoundError:
        st.error("Data files not found. Please run 'generate_daily_facts.py' first.")
        return None, None, None

df_wf, df_daily, df_var = load_data()

if df_wf is None:
    st.stop()

# =============================================================================
# SIDEBAR FILTERS
# =============================================================================
st.sidebar.header("Global Filters")

# Org Filter (Hierarchy Level 1)
all_orgs = sorted(df_wf['Inv Org'].unique())
selected_org = st.sidebar.selectbox("Select Inv Org", all_orgs)

# Filter dataframes by Org first
df_wf_sub = df_wf[df_wf['Inv Org'] == selected_org]
df_daily_sub = df_daily[df_daily['Inv Org'] == selected_org]
df_var_sub = df_var[df_var['Inv Org'] == selected_org]

# Item Filter (Hierarchy Level 2)
# Only show items available in the selected Org
avail_items = sorted(df_wf_sub['Item Code'].unique())
if not avail_items:
    st.sidebar.warning("No items found for this Org.")
    st.stop()
    
selected_item = st.sidebar.selectbox("Select Item Code", avail_items)

# Apply Item filter
df_wf_sub = df_wf_sub[df_wf_sub['Item Code'] == selected_item]
df_daily_sub = df_daily_sub[df_daily_sub['Item Code'] == selected_item]
df_var_sub = df_var_sub[df_var_sub['Item Code'] == selected_item]

st.sidebar.markdown("---")
st.sidebar.info(f"Loaded {len(df_wf)} waterfall rows.")

# =============================================================================
# TABS
# =============================================================================
tab1, tab2, tab3 = st.tabs(["Waterfall Analysis", "Supply vs Demand", "Forecast Variance"])

# --- TAB 1: WATERFALL ---
with tab1:
    st.subheader(f"Inventory Forecast Waterfall: {selected_item} @ {selected_org}")
    
    # User selects a target date to analyze
    # Get available dates for this item
    avail_dates = sorted(df_wf_sub['Date'].unique())
    if not avail_dates:
        st.warning("No waterfall data for this selection.")
    else:
        # Default to a date in the future? or first available?
        # Let's pick one in the middle or let user pick
        target_date = st.selectbox("Select Forecast Target Date", avail_dates, index=0)
        
        # Filter for this target date
        wf_chart_data = df_wf_sub[df_wf_sub['Date'] == target_date].sort_values('Snapshot Date')
        
        if wf_chart_data.empty:
            st.warning("No changes found for this date.")
        else:
            # Prepare Waterfall Plot
            # X: Snapshot Date
            # Y: Delta Inventory
            
            # Step 1: Get the baseline (Snapshot 1)
            first_row = wf_chart_data.iloc[0]
            
            base_inv = first_row['Tot.Inventory_previous']
            base_date = first_row['Previous Snapshot Date']
            
            # Dates list
            x_vals = [f"Base ({base_date.strftime('%m-%d')})"] + \
                     wf_chart_data['Snapshot Date'].dt.strftime('%m-%d').tolist() + ["Final"]
            
            # Measure types
            measure = ["absolute"] + ["relative"] * len(wf_chart_data) + ["total"]
            
            # Y values
            y_vals = [base_inv] + wf_chart_data['Delta_Inventory'].tolist() + [None] 
            
            fig = go.Figure(go.Waterfall(
                x = x_vals,
                measure = measure,
                y = y_vals,
                base = 0,
                decreasing = {"marker":{"color":"#EF553B"}},
                increasing = {"marker":{"color":"#00CC96"}},
                totals = {"marker":{"color":"#636EFA"}}
            ))
            
            fig.update_layout(
                title=f"Evolution of Inventory Forecast for {target_date.date()}", 
                waterfallgap = 0.3,
                xaxis=dict(type='category')
            )
            st.plotly_chart(fig, use_container_width=True)
            
            st.dataframe(wf_chart_data)

# --- TAB 2: SUPPLY vs DEMAND ---
with tab2:
    st.subheader("Supply & Demand Timeline")
    
    # Filter by snapshot? Usually we want to see the LATEST plan.
    # Get latest snapshot available
    all_snaps = sorted(df_daily_sub['Snapshot Date'].unique())
    selected_snap = st.selectbox("Select Forecast Snapshot", all_snaps, index=len(all_snaps)-1)
    
    ts_data = df_daily_sub[df_daily_sub['Snapshot Date'] == selected_snap].sort_values('Date')
    
    if ts_data.empty:
        st.write("No data.")
    else:
        # Line Chart: Two Lines
        fig2 = px.line(ts_data, x='Date', y=['Tot.Inventory_daily', 'Indep.Req_daily'],
                       labels={'value': 'Quantity', 'variable': 'Metric'},
                       title=f"Plan as of {pd.to_datetime(selected_snap).date()}")
        
        # Add fill for shortage?
        # Maybe complex for st.line_chart, use plotly
        # Add a zero line?
        
        st.plotly_chart(fig2, use_container_width=True)
        
        # Metrics
        total_short = ts_data[ts_data['Net_Inventory_vs_Demand'] < 0]['Net_Inventory_vs_Demand'].sum()
        st.metric("Total Cumulative Shortage (Daily Sum)", f"{total_short:,.0f}")

# --- TAB 3: VARIANCE ---
with tab3:
    st.subheader("Demand Forecast Variance")
    
    st.markdown("Metrics aggregated across all snapshots.")
    
    # df_var_sub is already Item/Org filtered
    st.dataframe(df_var_sub)
    
    if not df_var_sub.empty:
        # Scatter plot of CV vs Mean
        # But for one item there's limited scatter points (dates).
        # Could show CV over time?
        
        fig3 = px.scatter(df_var_sub, x='Date', y='cv_demand_forecast', 
                          size='mean_demand_forecast',
                          title="Demand Volatility (CV) over Time")
        st.plotly_chart(fig3, use_container_width=True)
