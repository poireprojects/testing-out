"""The scanning universe: ~450 liquid U.S. stocks and plain (non-leveraged) ETFs.

At runtime the list is filtered against the broker's asset list (tradable, active,
fractionable, U.S. equity on a major exchange). Leveraged/inverse ETFs and crypto
products are excluded on purpose: they conflict with the "no leverage, no crypto"
safety rules even though they trade like stocks.
"""
from __future__ import annotations

import re
from typing import Iterable

from ..models import AssetInfo

ETFS = """
SPY QQQ IWM DIA VOO VTI IVV RSP XLK XLF XLE XLV XLY XLP XLI XLB XLU XLRE XLC SMH SOXX IGV
ARKK KRE KBE XBI IBB XOP XME XHB ITB XRT GDX GDXJ GLD SLV USO UNG TLT IEF SHY HYG LQD EEM
EFA FXI EWZ EWJ INDA VNQ VEA VWO IEMG MCHI KWEB JETS TAN ICLN LIT URA COPX
"""

STOCKS = """
AAPL MSFT NVDA AMZN GOOGL GOOG META TSLA AVGO BRK.B JPM LLY V UNH XOM MA JNJ PG HD COST ABBV
MRK CVX KO PEP ADBE CRM WMT BAC NFLX AMD TMO ACN MCD CSCO ABT LIN DHR ORCL DIS WFC TXN INTU
PM VZ CMCSA NEE AMGN IBM QCOM HON UNP CAT GE AMAT LOW SPGI RTX BA GS NKE ELV UPS ISRG T BKNG
SBUX MS PLD DE BLK MDT GILD AXP LMT SYK ADP TJX VRTX MMC CVS C REGN CB ADI LRCX MDLZ SCHW PGR
CI MU BSX ETN ZTS SO PANW BX KLAC SNPS DUK CDNS SHW AMT EQIX ICE CME ITW MO NOC SLB CL FI WM
PYPL USB APD CSX MCK HCA PNC EOG GD MMM AON FCX EMR ORLY NSC TGT MAR PSX MPC CMG ROP AJG NXPI
ECL AZO MCO CTAS PH APH ADSK TT TDG HLT AIG CARR MSI COF FDX PCAR TRV OXY WELL GM F SRE MET AFL
PSA JCI KMB SPG DLR O ALL TEL ROST AEP KMI WMB PAYX HUM D CCI MSCI GIS IDXX FTNT YUM AMP LHX
PRU CPRT KHC A EW DXCM BIIB CTSH EXC MNST KR CMI OTIS IQV ODFL CTVA DOW EA FAST VRSK GWW RSG
XEL VLO PEG ED KDP DD HPQ BKR CSGP ANET ON MCHP ROK GLW DHI LEN NUE HAL ACGL CDW IT DVN FANG
WEC KEYS GPN VMC MLM EFX ZBH WTW AWK TSCO MTD EBAY HIG RMD CBRE DAL UAL LUV AAL CCL RCL NCLH
ABNB UBER LYFT DASH SNOW PLTR SHOP XYZ HOOD ROKU ZM DOCU CRWD ZS DDOG NET MDB OKTA TEAM WDAY
NOW TTD SNAP PINS SPOT RBLX U AFRM SOFI UPST RIVN LCID NIO XPEV LI BABA JD PDD BIDU TSM ASML
ARM SMCI DELL HPE INTC WDC STX MRVL TER ENPH FSLR DG DLTR BBY ULTA LULU DECK CROX ETSY CHWY
EXPE MGM WYNN LVS CZR DKNG KEY RF HBAN CFG FITB MTB TFC ZION ALLY SYF COP APA CTRA EQT AR RRC
OVV PFE BMY MRNA BNTX TEVA CNC MOH ALGN ILMN HOLX BAX BDX STE WAT GEV GEHC JBHT CHRW EXPD URI
PWR J LDOS BAH HII TXT AXON FICO ZBRA AKAM FFIV NTAP PSTG SWKS QRVO MPWR ENTG COHR LSCC CELH
HSY SJM CAG CPB HRL TSN ADM BG STZ TAP EL CLX CHD KVUE BURL GAP ANF AEO URBN M DKS TMUS CHTR
WBD TTWO VICI AVB EQR ARE EIX PCG ETR FE PPL CNP NRG VST CEG NEM PPG IFF ALB MOS CF LYB STLD
CLF AA GME RDDT ASTS IONQ OKLO CAVA DUOL APP VRT CPNG GRAB SE MELI NU TCOM STLA TM ALK JBLU
HUBS TWLO PATH GTLB ESTC CFLT S BILL PAYC PCTY FIS
"""

# Market-context symbols always fetched (not necessarily traded).
CONTEXT_SYMBOLS = ("SPY", "QQQ")

_EXCLUDE_NAME = re.compile(
    r"(?i)\b(2x|3x|-1x|-2x|-3x|ultra|ultrapro|leveraged|inverse|short|bear|bull|daily|bitcoin|ether|ethereum|crypto|"
    r"solana|volatility|vix)\b"
)
_ALLOWED_EXCHANGES = {"NYSE", "NASDAQ", "ARCA", "BATS", "AMEX", "NYSEARCA"}
_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


def curated_universe() -> list[str]:
    seen: dict[str, None] = {}
    for s in (ETFS + " " + STOCKS).split():
        seen.setdefault(s.strip().upper(), None)
    return list(seen)


def is_safe_symbol(symbol: str) -> bool:
    """Rejects options-style, crypto-pair and otherwise unsupported symbols."""
    return bool(_SYMBOL_RE.match(symbol or ""))


def asset_allowed(asset: AssetInfo) -> tuple[bool, str]:
    if asset.asset_class != "us_equity":
        return False, "not a U.S. stock or ETF"
    if not asset.tradable or asset.status != "active":
        return False, "not tradable"
    if not asset.fractionable:
        return False, "fractional shares not supported"
    if asset.exchange.upper() not in _ALLOWED_EXCHANGES:
        return False, f"exchange {asset.exchange} not supported"
    if _EXCLUDE_NAME.search(asset.name or ""):
        return False, "leveraged, inverse or crypto product"
    if not is_safe_symbol(asset.symbol):
        return False, "unsupported symbol format"
    return True, ""


def build_universe(assets: Iterable[AssetInfo] | None, size: int, extra: Iterable[str] = ()) -> tuple[list[str], dict[str, str]]:
    """Returns (universe, rejected{symbol: reason}). If `assets` is None (demo/offline)
    the curated list is used without broker filtering."""
    curated = curated_universe()
    candidates = list(dict.fromkeys([*curated, *[e.upper() for e in extra]]))
    rejected: dict[str, str] = {}
    if assets is None:
        out = [s for s in candidates if is_safe_symbol(s)]
        return out[:size], rejected
    by_sym = {a.symbol: a for a in assets}
    out = []
    for sym in candidates:
        a = by_sym.get(sym)
        if a is None:
            rejected[sym] = "not offered by broker"
            continue
        ok, why = asset_allowed(a)
        if ok:
            out.append(sym)
        else:
            rejected[sym] = why
    for ctx in CONTEXT_SYMBOLS:
        if ctx not in out and ctx in by_sym:
            out.insert(0, ctx)
    return out[:size], rejected
