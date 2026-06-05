"""Snowflake data loading for Pilar 2 rapporten."""

import sys
from pathlib import Path
from datetime import date, timedelta

import pandas as pd
import streamlit as st

# Reuse Snowflake connection from StorebrandAM-Ops-MCP
_MCP_PATH = Path(r"C:\Users\DUP\StorebrandAM-Ops-MCP")
if str(_MCP_PATH) not in sys.path:
    sys.path.insert(0, str(_MCP_PATH))


def _get_sf():
    """Get a SnowflakeResource, importing db.py from MCP project."""
    # Import here to avoid circular import at module load time
    import dotenv
    dotenv.load_dotenv(_MCP_PATH / ".env", override=False)
    from db import SnowflakeResource  # noqa: PLC0415
    return SnowflakeResource()


@st.cache_data(ttl=3600, show_spinner=False)
def load_portfolios(portfolio_group: str) -> pd.DataFrame:
    """Hent alle porteføljer for en porteføljegruppe."""
    sf = _get_sf()
    return sf.query(f"""
        SELECT
            PORTFOLIO_SHORT_NAME,
            PORTFOLIO_NAME,
            PORTFOLIO_CURRENCY,
            PORTFOLIO_TYPE
        FROM DWH_SAM.CONFORMED_DIM.DIM_PORTFOLIO
        WHERE PORTFOLIO_GROUP_SHORT_NAME = '{portfolio_group}'
          AND PORTFOLIO_TYPE = 'Own'
        ORDER BY PORTFOLIO_SHORT_NAME
    """)


@st.cache_data(ttl=3600, show_spinner=False)
def load_dividends(portfolio_group: str, year: int) -> pd.DataFrame:
    """Hent alle utbytte-transaksjoner for porteføljegruppen i rapporteringsåret."""
    sf = _get_sf()
    return sf.query(f"""
        SELECT
            p.PORTFOLIO_SHORT_NAME,
            p.PORTFOLIO_NAME,
            dp.DATE_TIME::DATE                         AS utdelingsdato,
            s.ISIN,
            s.SEC_REF                                  AS id,
            s.SEC_NAME                                 AS selskap,
            s.INSTRUMENT_TYPE                          AS verdipapirtype,
            s.COUNTRY_OF_ISSUE                         AS land,
            t.BUS_TRANS_CODE                           AS trans_kode,
            SUM(t.PAYMENT_AMOUNT_PC)                   AS utbytte_nok,
            s.ISSUED_VOLUME                            AS totalt_utstedt
        FROM DWH_SAM.TRANSACTIONS.FACT_TRANSACTIONS t
        JOIN DWH_SAM.CONFORMED_DIM.DIM_PORTFOLIO p
          ON t.S_FK_DIM_PORTFOLIO = p.S_PK_DIM_PORTFOLIO
        JOIN DWH_SAM.CONFORMED_DIM.DIM_DATE dp
          ON t.S_FK_DIM_DATE_PAYMENT = dp.S_PK_DIM_DATE
        JOIN DWH_SAM.CONFORMED_DIM.DIM_SECURITY s
          ON t.S_FK_DIM_SECURITY = s.S_PK_DIM_SECURITY
        WHERE p.PORTFOLIO_GROUP_SHORT_NAME = '{portfolio_group}'
          AND t.TRANSACTION_CANCELLATION_NO = 0
          AND t.BUS_TRANS_CODE = 'Dividend'
          AND s.INSTRUMENT_TYPE = 'Equity'
          AND dp.YEAR_NUMBER = {year}
        GROUP BY
            p.PORTFOLIO_SHORT_NAME, p.PORTFOLIO_NAME,
            dp.DATE_TIME, s.ISIN, s.SEC_REF, s.SEC_NAME,
            s.INSTRUMENT_TYPE, s.COUNTRY_OF_ISSUE,
            t.BUS_TRANS_CODE, s.ISSUED_VOLUME
        ORDER BY p.PORTFOLIO_SHORT_NAME, dp.DATE_TIME, s.ISIN
    """)


@st.cache_data(ttl=3600, show_spinner=False)
def load_transactions(
    portfolio_group: str,
    from_date_str: str,
    to_date_str: str,
    isin_tuple: tuple[str, ...],
) -> pd.DataFrame:
    """Hent Buy/Sell-transaksjoner for LIFO-beregning (filtrert på relevante ISIN)."""
    if not isin_tuple:
        return pd.DataFrame()
    sf = _get_sf()
    isin_list = "', '".join(isin_tuple)
    return sf.query(f"""
        SELECT
            p.PORTFOLIO_SHORT_NAME,
            s.ISIN,
            t.BUS_TRANS_CODE,
            dt.DATE_TIME::DATE  AS trade_date,
            t.NOMINAL,
            t.PAYMENT_AMOUNT_PC,
            t.COST_VALUE_PC,
            t.P_OR_L_BOOK_CCY_PC,
            t.P_OR_L_BOOK_SEC_PC,
            s.SEC_NAME          AS selskap,
            s.SEC_REF           AS id
        FROM DWH_SAM.TRANSACTIONS.FACT_TRANSACTIONS t
        JOIN DWH_SAM.CONFORMED_DIM.DIM_PORTFOLIO p
          ON t.S_FK_DIM_PORTFOLIO = p.S_PK_DIM_PORTFOLIO
        JOIN DWH_SAM.CONFORMED_DIM.DIM_DATE dt
          ON t.S_FK_DIM_DATE_TRADE = dt.S_PK_DIM_DATE
        JOIN DWH_SAM.CONFORMED_DIM.DIM_SECURITY s
          ON t.S_FK_DIM_SECURITY = s.S_PK_DIM_SECURITY
        WHERE p.PORTFOLIO_GROUP_SHORT_NAME = '{portfolio_group}'
          AND t.TRANSACTION_CANCELLATION_NO = 0
          AND t.BUS_TRANS_CODE IN ('Buy', 'Sell', 'CapRed')
          AND s.INSTRUMENT_TYPE = 'Equity'
          AND s.ISIN IN ('{isin_list}')
          AND dt.DATE_TIME >= '{from_date_str}'
          AND dt.DATE_TIME <= '{to_date_str}'
        ORDER BY p.PORTFOLIO_SHORT_NAME, s.ISIN, dt.DATE_TIME
    """)


@st.cache_data(ttl=3600, show_spinner=False)
def load_opening_balances(
    portfolio_group: str,
    from_date_str: str,
    to_date_str: str,
    isin_tuple: tuple[str, ...],
) -> pd.DataFrame:
    """Hent daglig beholdning for åpningsbeholdning-beregning i LIFO-kjeden."""
    if not isin_tuple:
        return pd.DataFrame()
    sf = _get_sf()
    isin_list = "', '".join(isin_tuple)
    return sf.query(f"""
        SELECT
            h.PORTFOLIO_SHORT_NAME,
            h.ISIN,
            h.HOLDING_DATE,
            SUM(h.NOMINAL) AS nominal
        FROM DDS_SAM.HOLDINGS.HOLDINGS_PFC_EOD h
        JOIN DWH_SAM.CONFORMED_DIM.DIM_PORTFOLIO p
          ON h.PORTFOLIO_SHORT_NAME = p.PORTFOLIO_SHORT_NAME
        WHERE p.PORTFOLIO_GROUP_SHORT_NAME = '{portfolio_group}'
          AND h.ISIN IN ('{isin_list}')
          AND h.INSTRUMENT_TYPE = 'Equity'
          AND h.PFCM = 'DAILY'
          AND h.HOLDING_DATE BETWEEN '{from_date_str}' AND '{to_date_str}'
        GROUP BY h.PORTFOLIO_SHORT_NAME, h.ISIN, h.HOLDING_DATE
        ORDER BY h.PORTFOLIO_SHORT_NAME, h.ISIN, h.HOLDING_DATE
    """)


@st.cache_data(ttl=3600, show_spinner=False)
def load_sales(portfolio_group: str, year: int) -> pd.DataFrame:
    """Hent alle aksjesalg for gevinst/tap-rapporten (Fane 3)."""
    sf = _get_sf()
    return sf.query(f"""
        SELECT
            p.PORTFOLIO_SHORT_NAME,
            p.PORTFOLIO_NAME,
            dt.DATE_TIME::DATE          AS trade_date,
            dp.DATE_TIME::DATE          AS payment_date,
            s.ISIN,
            s.SEC_REF                   AS id,
            s.SEC_NAME                  AS selskap,
            s.INSTRUMENT_TYPE           AS verdipapirtype,
            s.COUNTRY_OF_ISSUE          AS land,
            ABS(t.NOMINAL)              AS antall,
            t.PAYMENT_AMOUNT_PC         AS salgsproveny_nok,
            t.COST_VALUE_PC             AS kostverdi_nok,
            t.P_OR_L_BOOK_SEC_PC        AS pl_kurs_nok,
            t.P_OR_L_BOOK_CCY_PC        AS pl_valuta_nok,
            (t.P_OR_L_BOOK_SEC_PC
             + t.P_OR_L_BOOK_CCY_PC)   AS pl_total_nok
        FROM DWH_SAM.TRANSACTIONS.FACT_TRANSACTIONS t
        JOIN DWH_SAM.CONFORMED_DIM.DIM_PORTFOLIO p
          ON t.S_FK_DIM_PORTFOLIO = p.S_PK_DIM_PORTFOLIO
        JOIN DWH_SAM.CONFORMED_DIM.DIM_DATE dt
          ON t.S_FK_DIM_DATE_TRADE = dt.S_PK_DIM_DATE
        JOIN DWH_SAM.CONFORMED_DIM.DIM_DATE dp
          ON t.S_FK_DIM_DATE_PAYMENT = dp.S_PK_DIM_DATE
        JOIN DWH_SAM.CONFORMED_DIM.DIM_SECURITY s
          ON t.S_FK_DIM_SECURITY = s.S_PK_DIM_SECURITY
        WHERE p.PORTFOLIO_GROUP_SHORT_NAME = '{portfolio_group}'
          AND t.TRANSACTION_CANCELLATION_NO = 0
          AND t.BUS_TRANS_CODE = 'Sell'
          AND s.INSTRUMENT_TYPE = 'Equity'
          AND dt.YEAR_NUMBER = {year}
        ORDER BY p.PORTFOLIO_SHORT_NAME, dt.DATE_TIME, s.ISIN
    """)


def compute_date_ranges(
    dividends_df: pd.DataFrame,
) -> tuple[str, str, str, str]:
    """
    Beregn nødvendige datointervaller for LIFO-analyse.

    Returnerer:
        - lifo_from_str: Tidligste dato for transaksjoner (13 mnd før første utbytte)
        - lifo_to_str:   Seneste dato for transaksjoner (siste utbetalingsdato)
        - hold_from_str: Tidligste dato for beholdningssnapshot (litt før lifo_from)
        - hold_to_str:   Seneste dato for beholdningssnapshot
    """
    dates = pd.to_datetime(dividends_df["utdelingsdato"])
    min_div = dates.min().date()
    max_div = dates.max().date()

    lifo_from = min_div - timedelta(days=400)  # 13 mnd + buffer
    lifo_to = max_div
    hold_from = lifo_from - timedelta(days=10)  # buffer for å finne nærmeste handelsdag
    hold_to = lifo_from + timedelta(days=5)

    return (
        lifo_from.strftime("%Y-%m-%d"),
        lifo_to.strftime("%Y-%m-%d"),
        hold_from.strftime("%Y-%m-%d"),
        hold_to.strftime("%Y-%m-%d"),
    )
