"""
LIFO-beregningsmotor for Pilar 2 eierperiode-vurdering.

Metodikk (jf. suppleringsskatteforskriften § 3-2-1 bokstav b nr. 1):
- LIFO: siste kjøpte lot selges/konsumeres først ved salg
- 13-månedersvindu: kjøp/salg innenfor 13 måneder (395 dager) fra utbetalingsdato
- Åpningsbeholdning: netto posisjon ved vindu-start — alltid fritatt (≥ 13 mnd gammel)
- Fritatt andel: lot med eiertid ≥ 365 dager på utbetalingsdato
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import pandas as pd

EXEMPT_DAYS = 365        # 12-månedersterskel for fritak
WINDOW_DAYS = 395        # 13-månedersvindu


# ---------------------------------------------------------------------------
# Dataklasser
# ---------------------------------------------------------------------------

@dataclass
class Lot:
    """Representerer ett kjøp-lot i LIFO-køen."""
    lot_id: int
    lot_type: str          # 'ÅpningsBeholdning' | 'Buy' | 'CapRed'
    acquisition_date: date
    original_nominal: float
    remaining: float
    status: str = "OVERLEVER"   # OVERLEVER | DELVIS_OVERLEVER | KONSUMERT


@dataclass
class AuditRow:
    """Én rad i LIFO-kjeden (Fane 2)."""
    portefolje: str
    isin: str
    utdelingsdato: date
    hendelse: str          # ÅpningsBeholdning | Buy | SALG | UTBYTTE | CapRed
    dato: Optional[date]
    antall: float
    lot_status: str
    eiertid_dager: Optional[int]
    eiertid_ok: Optional[bool]
    kumulert_beholdning: Optional[float]


@dataclass
class LifoResult:
    """Resultat av LIFO-beregning for ett utbytteevent."""
    beholdning_antall: float
    herav_eiertid_antall: float
    fritaksstatus: str     # FRITATT | DELVIS_FRITATT | IKKE_FRITATT | IKKE_EIER
    eiertid_andel: float   # 0.0 – 1.0
    opening_balance: float
    lots: list[Lot]
    audit_rows: list[AuditRow]


# ---------------------------------------------------------------------------
# Kjernefunksjon: LIFO for ett utbytteevent
# ---------------------------------------------------------------------------

def _to_date(val) -> date:
    """Konverter pandas Timestamp eller date til date."""
    if isinstance(val, date):
        return val
    return val.date()


def calculate_lifo_event(
    portfolio: str,
    isin: str,
    dividend_date: date,
    transactions_df: pd.DataFrame,
    opening_balance: float,
    window_start: date,
) -> LifoResult:
    """
    Kjør LIFO-beregning for ett utbytteevent.

    Args:
        portfolio:        Porteføljekode (f.eks. 'P350AKE')
        isin:             ISIN for verdipapiret
        dividend_date:    Utbetalingsdato for utbyttet
        transactions_df:  DataFrame med Buy/Sell for dette (portfolio, isin)-paret,
                          sortert etter trade_date stigende
        opening_balance:  Netto beholdning ved window_start (fra holdings-snapshot)
        window_start:     Vindu-startdato = dividend_date - WINDOW_DAYS
    """
    lots: list[Lot] = []
    lot_counter = 0
    cumulative = 0.0

    # --- Åpningsbeholdning ---
    if opening_balance > 0:
        ob_date = window_start - timedelta(days=1)
        lots.append(Lot(
            lot_id=0,
            lot_type="ÅpningsBeholdning",
            acquisition_date=ob_date,
            original_nominal=opening_balance,
            remaining=opening_balance,
        ))
        cumulative = opening_balance

    # --- Bearbeid transaksjoner innenfor vinduet ---
    window_txns = transactions_df[
        (transactions_df["trade_date"] >= pd.Timestamp(window_start))
        & (transactions_df["trade_date"] <= pd.Timestamp(dividend_date))
    ].sort_values("trade_date").reset_index(drop=True)

    txn_lot_map: dict[int, Optional[int]] = {}  # txn index → lot_id (for Buy)

    for idx, row in window_txns.iterrows():
        code = str(row["BUS_TRANS_CODE"])
        nominal = abs(float(row["NOMINAL"]))
        tdate = _to_date(row["trade_date"])

        if code == "Buy":
            lot_counter += 1
            lot = Lot(
                lot_id=lot_counter,
                lot_type="Buy",
                acquisition_date=tdate,
                original_nominal=nominal,
                remaining=nominal,
            )
            lots.append(lot)
            txn_lot_map[idx] = lot_counter
            cumulative += nominal

        elif code in ("Sell", "CapRed"):
            # LIFO: konsum fra nyeste lot
            remaining_sell = nominal
            for lot in reversed(lots):
                if lot.status == "KONSUMERT" or remaining_sell <= 0:
                    continue
                if lot.remaining <= remaining_sell:
                    remaining_sell -= lot.remaining
                    lot.remaining = 0.0
                    lot.status = "KONSUMERT"
                else:
                    lot.remaining -= remaining_sell
                    lot.status = "DELVIS_OVERLEVER"
                    remaining_sell = 0.0
            cumulative -= nominal
            txn_lot_map[idx] = None  # Salg har ikke eget lot

    # --- Beregn overlevende beholdning og fritatt andel ---
    exempt_threshold = dividend_date - timedelta(days=EXEMPT_DAYS)
    total_surviving = 0.0
    exempt_surviving = 0.0

    for lot in lots:
        if lot.status != "KONSUMERT":
            total_surviving += lot.remaining
            acq = lot.acquisition_date
            if lot.lot_type == "ÅpningsBeholdning" or acq <= exempt_threshold:
                exempt_surviving += lot.remaining

    # --- Fritaksstatus ---
    if total_surviving <= 0:
        status = "IKKE_EIER"
        eiertid_andel = 0.0
    elif exempt_surviving <= 0:
        status = "IKKE_FRITATT"
        eiertid_andel = 0.0
    elif exempt_surviving >= total_surviving:
        status = "FRITATT"
        eiertid_andel = 1.0
    else:
        status = "DELVIS_FRITATT"
        eiertid_andel = exempt_surviving / total_surviving

    # --- Bygg audit trail (Fane 2) ---
    audit_rows = _build_audit_trail(
        portfolio=portfolio,
        isin=isin,
        dividend_date=dividend_date,
        opening_balance=opening_balance,
        window_start=window_start,
        lots=lots,
        window_txns=window_txns,
        txn_lot_map=txn_lot_map,
        total_surviving=total_surviving,
    )

    return LifoResult(
        beholdning_antall=total_surviving,
        herav_eiertid_antall=exempt_surviving,
        fritaksstatus=status,
        eiertid_andel=eiertid_andel,
        opening_balance=opening_balance,
        lots=lots,
        audit_rows=audit_rows,
    )


def _build_audit_trail(
    portfolio: str,
    isin: str,
    dividend_date: date,
    opening_balance: float,
    window_start: date,
    lots: list[Lot],
    window_txns: pd.DataFrame,
    txn_lot_map: dict[int, Optional[int]],
    total_surviving: float,
) -> list[AuditRow]:
    """Bygg detaljerte audit-trail-rader for LIFO-kjeden (Fane 2)."""
    rows: list[AuditRow] = []
    exempt_threshold = dividend_date - timedelta(days=EXEMPT_DAYS)

    # -- Rad 1: Åpningsbeholdning --
    if opening_balance > 0:
        ob_lot = next((l for l in lots if l.lot_type == "ÅpningsBeholdning"), None)
        rows.append(AuditRow(
            portefolje=portfolio,
            isin=isin,
            utdelingsdato=dividend_date,
            hendelse="ÅpningsBeholdning",
            dato=window_start - timedelta(days=1),
            antall=ob_lot.remaining if ob_lot else opening_balance,
            lot_status=ob_lot.status if ob_lot else "OVERLEVER",
            eiertid_dager=EXEMPT_DAYS + (window_start - date(window_start.year, 1, 1)).days,
            eiertid_ok=True,
            kumulert_beholdning=opening_balance,
        ))

    # -- Rader per transaksjon i vinduet --
    cumulative = opening_balance
    lot_by_id = {lot.lot_id: lot for lot in lots}

    for idx, row in window_txns.iterrows():
        code = str(row["BUS_TRANS_CODE"])
        nominal = abs(float(row["NOMINAL"]))
        tdate = _to_date(row["trade_date"])
        eiertid = (dividend_date - tdate).days

        if code == "Buy":
            cumulative += nominal
            lot_id = txn_lot_map.get(idx)
            lot = lot_by_id.get(lot_id) if lot_id else None
            rows.append(AuditRow(
                portefolje=portfolio,
                isin=isin,
                utdelingsdato=dividend_date,
                hendelse="Buy",
                dato=tdate,
                antall=nominal,
                lot_status=lot.status if lot else "OVERLEVER",
                eiertid_dager=eiertid,
                eiertid_ok=eiertid >= EXEMPT_DAYS,
                kumulert_beholdning=cumulative,
            ))
        elif code == "Sell":
            cumulative -= nominal
            rows.append(AuditRow(
                portefolje=portfolio,
                isin=isin,
                utdelingsdato=dividend_date,
                hendelse="SALG",
                dato=tdate,
                antall=-nominal,
                lot_status="SALG",
                eiertid_dager=None,
                eiertid_ok=None,
                kumulert_beholdning=cumulative,
            ))
        elif code == "CapRed":
            cumulative -= nominal
            rows.append(AuditRow(
                portefolje=portfolio,
                isin=isin,
                utdelingsdato=dividend_date,
                hendelse="CapRed",
                dato=tdate,
                antall=-nominal,
                lot_status="SALG",
                eiertid_dager=None,
                eiertid_ok=None,
                kumulert_beholdning=cumulative,
            ))

    # -- Oppsummeringsrad: UTBYTTE --
    rows.append(AuditRow(
        portefolje=portfolio,
        isin=isin,
        utdelingsdato=dividend_date,
        hendelse="UTBYTTE (Dividend)",
        dato=dividend_date,
        antall=total_surviving,
        lot_status="UTBYTTE (Dividend)",
        eiertid_dager=None,
        eiertid_ok=None,
        kumulert_beholdning=total_surviving,
    ))

    return rows


# ---------------------------------------------------------------------------
# Kjør full analyse for alle utbytteevent
# ---------------------------------------------------------------------------

def run_lifo_analysis(
    dividends_df: pd.DataFrame,
    transactions_df: pd.DataFrame,
    holdings_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Kjør LIFO-analyse for alle utbytteevent i dividends_df.

    Returns:
        fane1_df: Konklusjonstabell (én rad per utbytteevent)
        fane2_df: Detaljert LIFO-kjede (audit trail)
    """
    fane1_rows: list[dict] = []
    fane2_rows: list[dict] = []

    # Pre-indekser for raskere oppslag
    txn_indexed = (
        transactions_df.set_index(["PORTFOLIO_SHORT_NAME", "ISIN"])
        if not transactions_df.empty
        else pd.DataFrame()
    )
    hold_indexed = (
        holdings_df.set_index(["PORTFOLIO_SHORT_NAME", "ISIN"])
        if not holdings_df.empty
        else pd.DataFrame()
    )

    for _, div in dividends_df.iterrows():
        portfolio = str(div["PORTFOLIO_SHORT_NAME"])
        isin = str(div["ISIN"])
        div_date = _to_date(div["utdelingsdato"])
        window_start = div_date - timedelta(days=WINDOW_DAYS)

        # Filtrer transaksjoner for dette (portfolio, isin)-paret
        if not txn_indexed.empty and (portfolio, isin) in txn_indexed.index:
            txns = txn_indexed.loc[[(portfolio, isin)]].reset_index()
        else:
            txns = pd.DataFrame(columns=transactions_df.columns)

        # Hent åpningsbeholdning (nærmeste handelsdag ≤ window_start)
        opening_balance = 0.0
        if not hold_indexed.empty and (portfolio, isin) in hold_indexed.index:
            hold_rows = hold_indexed.loc[[(portfolio, isin)]].reset_index()
            hold_rows["HOLDING_DATE"] = pd.to_datetime(hold_rows["HOLDING_DATE"])
            candidates = hold_rows[
                hold_rows["HOLDING_DATE"] <= pd.Timestamp(window_start)
            ]
            if not candidates.empty:
                newest = candidates.sort_values("HOLDING_DATE").iloc[-1]
                opening_balance = float(newest["nominal"])
                # Validering: forkast hvis snapshot er mer enn 30 dager gammelt
                staleness = (window_start - newest["HOLDING_DATE"].date()).days
                if staleness > 30:
                    opening_balance = 0.0

        # Kjør LIFO
        result = calculate_lifo_event(
            portfolio=portfolio,
            isin=isin,
            dividend_date=div_date,
            transactions_df=txns,
            opening_balance=opening_balance,
            window_start=window_start,
        )

        # --- Fane 1-rad ---
        utbytte_nok = float(div["utbytte_nok"]) if div["utbytte_nok"] is not None else 0.0
        totalt_utstedt = div["totalt_utstedt"]
        beholdning = result.beholdning_antall
        herav_eiertid = result.herav_eiertid_antall
        eierandel = (
            beholdning / float(totalt_utstedt) if totalt_utstedt and float(totalt_utstedt) > 0 else None
        )
        fritatt_belop = utbytte_nok * result.eiertid_andel

        fane1_rows.append({
            "Portefølje":                   portfolio,
            "Portefølje navn":              div["PORTFOLIO_NAME"],
            "Utdelingsdato":                div_date,
            "ISIN":                         isin,
            "ID":                           div["id"],
            "Selskap":                      div["selskap"],
            "Verdipapirtype":               div["verdipapirtype"],
            "Land":                         div["land"],
            "Utbytte trans.kode":           div["trans_kode"],
            "Utbytte NOK":                  round(utbytte_nok, 2),
            "Beholdning (antall)":          round(beholdning, 0),
            "Herav eiertid > 12 mnd (antall)": round(herav_eiertid, 0),
            "Totalt utstedt (antall)":      int(totalt_utstedt) if totalt_utstedt else None,
            "Estimert eierandel (%)":       round(eierandel * 100, 4) if eierandel else None,
            "Fritaksstatus eiertid":        result.fritaksstatus,
            "Eiertid > 12 mnd andel":       f"{round(result.eiertid_andel * 100, 1)}%",
            "Eiertid > 12 mnd beløp NOK":  round(fritatt_belop, 2),
        })

        # --- Fane 2-rader ---
        for ar in result.audit_rows:
            fane2_rows.append({
                "Portefølje":           ar.portefolje,
                "ISIN":                 ar.isin,
                "Utdelingsdato":        ar.utdelingsdato,
                "Hendelse":             ar.hendelse,
                "Dato":                 ar.dato,
                "Antall":               round(ar.antall, 0) if ar.antall is not None else None,
                "Lot-status":           ar.lot_status,
                "Eiertid (dager)":      ar.eiertid_dager,
                "Eiertid ≥ 365 dager":  ar.eiertid_ok,
                "Kumulert beholdning":  round(ar.kumulert_beholdning, 0) if ar.kumulert_beholdning is not None else None,
            })

    return pd.DataFrame(fane1_rows), pd.DataFrame(fane2_rows)
