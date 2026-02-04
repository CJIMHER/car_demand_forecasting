import streamlit as st
import pandas as pd
from gt_fetch import fetch_trends_monthly
# import other modules: transform_utils, lag_correlations, modeling

st.title("Análisis y recomendación de modelo para demanda de vehículos")

with st.form("input_form"):
    col1, col2 = st.columns(2)
    with col1:
        start_ym = st.text_input("Fecha inicio (YYYY-MM)", value="2020-01")
        end_ym = st.text_input("Fecha fin (YYYY-MM)", value="2023-12")
        ciudad = st.text_input("Ciudad", value="")
    with col2:
        marca = st.text_input("Marca", value="Toyota")
        modelo = st.text_input("Modelo", value="Corolla")
        estado = st.selectbox("VO/VN", ["VO", "VN"])
    uploaded = st.file_uploader("Sube CSV o Excel con FECHA y VENTAS_MENSUALES", type=['csv','xlsx'])
    submitted = st.form_submit_button("Ejecutar análisis")

if submitted:
    if uploaded is None:
        st.error("Sube el fichero con las columnas FECHA y VENTAS_MENSUALES")
    else:
        df = pd.read_csv(uploaded) if uploaded.name.endswith('.csv') else pd.read_excel(uploaded)
        st.write("Preview", df.head())
        keyword = f"{marca} {modelo}"
        st.info(f"Descargando Google Trends para '{keyword}'...")
        gt_series = fetch_trends_monthly(keyword, start_ym, end_ym, geo='', cat=None)
        st.write("GT sample", gt_series.head())
        # llamar a pipeline de preprocesado y modelos (no mostrado aquí)
        st.success("Pipeline ejecutado. Se muestran resultados...")