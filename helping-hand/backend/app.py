from flask import Flask, render_template, request, jsonify
import os
import time
import requests
import feedparser
from datetime import datetime, timedelta

# Normalization anchors
CFR_MIN, CFR_MAX = 0.0, 0.90
R0_MIN, R0_MAX = 0.5, 18.0
TRANSMISSION_MIN, TRANSMISSION_MAX = 1.0, 3.0
VACCINE_MIN, VACCINE_MAX = 0.0, 1.0

def normalize(value, min_val, max_val):
    if max_val == min_val:
        return 0.0
    return max(0.0, min(1.0, (value - min_val) / (max_val - min_val)))

app = Flask(__name__, template_folder="frontend/templates")

# -----------------------------
# CONFIG
# -----------------------------

USER_AGENT = "helping-hand/1.0 (contact: ngovind@unc.edu)"

WHO_RSS_URL = "https://www.who.int/feeds/entity/csr/don/en/rss.xml"
CDC_RSS_URL = "https://tools.cdc.gov/api/v2/resources/media/403372.rss"

NONPROFITS_PATH = os.path.join("data", "nonprofits.json")
PANDEMICS_PATH = os.path.join("data", "pandemics.json")
US_STATES_GEOJSON_PATH = os.path.join("data", "us_states_min.geojson")

RSS_CACHE_TTL_SECONDS = 10 * 60
GEOCODE_CACHE_TTL_SECONDS = 60 * 60
FLUVIEW_CACHE_TTL_SECONDS = 24 * 60 * 60  # refresh once per day

RSS_CACHE = {}
GEOCODE_CACHE = {}
FLUVIEW_CACHE = {"ts": 0, "data": None}  # single global entry; refreshed daily

# -----------------------------
# REGIONAL MALARIA R0 MAP
# Source: WHO Global Health Observatory via World Bank (2026),
# processed by Our World in Data — incidence-of-malaria.csv (2024 data)
# Tiers based on incidence per 1,000 population at risk:
#   sub_saharan_africa : >100  (core high-burden belt)
#   high_endemic       : 10-100 (fringe Africa, parts of Asia/Americas)
#   low_endemic        : 1-10  (South Asia, Latin America medium burden)
#   default            : <1    (low/no transmission; US, Europe, most of Asia)
# R0 values reflect regional transmission intensity, capped at 18.0
# (raw Ross-Macdonald R0 ~100 in high-transmission settings;
#  not comparable to direct-transmission R0 — see composite_score())
# -----------------------------

COUNTRY_REGION_MAP = {
    # Sub-Saharan Africa core — incidence >100/1000 (2024 WHO data)
    "ago": "sub_saharan_africa",  # Angola          258.9
    "bdi": "sub_saharan_africa",  # Burundi         314.9
    "ben": "sub_saharan_africa",  # Benin           354.3
    "bfa": "sub_saharan_africa",  # Burkina Faso    353.5
    "caf": "sub_saharan_africa",  # Central African Republic 343.8
    "civ": "sub_saharan_africa",  # Cote d'Ivoire   268.0
    "cmr": "sub_saharan_africa",  # Cameroon        260.5
    "cod": "sub_saharan_africa",  # DR Congo        321.9
    "cog": "sub_saharan_africa",  # Congo           221.0
    "eri": "sub_saharan_africa",  # Eritrea         102.4
    "eth": "sub_saharan_africa",  # Ethiopia        138.5
    "gab": "sub_saharan_africa",  # Gabon           187.0
    "gha": "sub_saharan_africa",  # Ghana           195.8
    "gin": "sub_saharan_africa",  # Guinea          286.1
    "gnb": "sub_saharan_africa",  # Guinea-Bissau   110.1
    "gnq": "sub_saharan_africa",  # Equatorial Guinea 227.7
    "lbr": "sub_saharan_africa",  # Liberia         172.3
    "mdg": "sub_saharan_africa",  # Madagascar      255.5
    "mli": "sub_saharan_africa",  # Mali            346.2
    "moz": "sub_saharan_africa",  # Mozambique      295.1
    "mwi": "sub_saharan_africa",  # Malawi          294.5
    "ner": "sub_saharan_africa",  # Niger           305.1
    "nga": "sub_saharan_africa",  # Nigeria         294.3
    "png": "sub_saharan_africa",  # Papua New Guinea 151.8
    "slb": "sub_saharan_africa",  # Solomon Islands 224.3
    "sle": "sub_saharan_africa",  # Sierra Leone    282.7
    "ssd": "sub_saharan_africa",  # South Sudan     254.1
    "tcd": "sub_saharan_africa",  # Chad            206.1
    "tgo": "sub_saharan_africa",  # Togo            249.3
    "tza": "sub_saharan_africa",  # Tanzania        136.7
    "uga": "sub_saharan_africa",  # Uganda          264.2
    "zmb": "sub_saharan_africa",  # Zambia          252.4

    # High endemic — incidence 10-100/1000 (2024 WHO data)
    "afg": "high_endemic",  # Afghanistan  17.8
    "col": "high_endemic",  # Colombia     15.2
    "com": "high_endemic",  # Comoros      62.8
    "dji": "high_endemic",  # Djibouti     45.1
    "gmb": "high_endemic",  # Gambia       75.4
    "guy": "high_endemic",  # Guyana       38.7
    "hti": "high_endemic",  # Haiti         8.7
    "ken": "high_endemic",  # Kenya        74.2
    "mmr": "high_endemic",  # Myanmar      14.8
    "mrt": "high_endemic",  # Mauritania   69.6
    "nam": "high_endemic",  # Namibia       7.2
    "pak": "high_endemic",  # Pakistan     12.8
    "rwa": "high_endemic",  # Rwanda       77.1
    "sdn": "high_endemic",  # Sudan        98.3
    "sen": "high_endemic",  # Senegal      36.8
    "som": "high_endemic",  # Somalia      53.3
    "stp": "high_endemic",  # Sao Tome     30.1
    "ven": "high_endemic",  # Venezuela     7.8
    "vut": "high_endemic",  # Vanuatu      12.3
    "yem": "high_endemic",  # Yemen        52.2
    "zwe": "high_endemic",  # Zimbabwe     11.4

    # Low endemic — incidence 1-10/1000 (2024 WHO data)
    "bgd": "low_endemic",  # Bangladesh    0.86
    "bol": "low_endemic",  # Bolivia       3.83
    "bra": "low_endemic",  # Brazil        3.75
    "bwa": "low_endemic",  # Botswana      0.20
    "dom": "low_endemic",  # Dominican Rep 0.19
    "gtm": "low_endemic",  # Guatemala     0.21
    "hnd": "low_endemic",  # Honduras      0.23
    "idn": "low_endemic",  # Indonesia     2.23
    "ind": "low_endemic",  # India         1.48
    "irn": "low_endemic",  # Iran          2.18
    "kor": "low_endemic",  # South Korea   0.18
    "nic": "low_endemic",  # Nicaragua     1.04
    "pan": "low_endemic",  # Panama        3.50
    "per": "low_endemic",  # Peru          3.25
    "phl": "low_endemic",  # Philippines   0.35
    "prk": "low_endemic",  # North Korea   0.51
    "swz": "low_endemic",  # Eswatini      0.55
    "tha": "low_endemic",  # Thailand      0.58
}

# Regional R0 values for malaria
# Anchored to incidence tiers above; capped at R0_MAX (18.0)
MALARIA_REGIONAL_R0 = {
    "sub_saharan_africa": 18.0,  # highest transmission belt
    "high_endemic":        8.0,  # moderate transmission regions
    "low_endemic":         3.0,  # low transmission; near elimination in some
    "default":             1.5,  # minimal/no active transmission
}

# -----------------------------
# REGIONAL DENGUE R0 MAP
# Source: WHO 2024 Global Dengue Surveillance Report + WHO SEARO + CDC Areas with Risk of Dengue
# Tiers based on 2024 case burden and CDC frequent/continuous transmission classification:
#   very_high : >100k cases 2024 OR among WHO's 30 most endemic countries
#   high      : frequent/continuous transmission, significant reported burden
#   moderate  : sporadic/seasonal outbreaks, periodic transmission
#   default   : no endemic transmission; travel-associated cases only
# R0 values: dengue R0 ranges 2-6 depending on Aedes aegypti density and climate
# -----------------------------

DENGUE_REGIONAL_R0 = {
    "very_high": 6.0,   # highest Aedes aegypti density, year-round transmission
    "high":      4.0,   # frequent transmission, seasonal peaks
    "moderate":  2.0,   # sporadic; limited vector habitat
    "default":   0.5,   # no endemic transmission
}

DENGUE_COUNTRY_MAP = {
    # Very high — Americas: WHO 2024 top case-burden countries
    "br": "very_high",  # Brazil         3.6M+ cases 2024
    "ar": "very_high",  # Argentina      581k cases 2024
    "mx": "very_high",  # Mexico         558k cases 2024
    "co": "very_high",  # Colombia       320k cases 2024
    "py": "very_high",  # Paraguay       295k cases 2024
    "pe": "very_high",  # Peru           271k cases 2024
    "gt": "very_high",  # Guatemala      188k cases 2024
    "hn": "very_high",  # Honduras       177k cases 2024
    # Very high — Southeast Asia: WHO SEARO top-30 endemic
    "id": "very_high",  # Indonesia      257k cases 2024; top-30 endemic
    "in": "very_high",  # India          232k cases 2024; top-30 endemic
    "mm": "very_high",  # Myanmar        top-30 endemic WHO SEARO
    "th": "very_high",  # Thailand       top-30 endemic WHO SEARO
    "lk": "very_high",  # Sri Lanka      top-30 endemic WHO SEARO
    # Very high — Western Pacific
    "vn": "very_high",  # Vietnam        major burden WHO WPRO
    "my": "very_high",  # Malaysia       50k+ cases April 2024
    "ph": "very_high",  # Philippines    high endemic Western Pacific
    # High — frequent/continuous transmission
    "bd": "high",       # Bangladesh     significant SEA burden
    "np": "high",       # Nepal          SEA endemic
    "pk": "high",       # Pakistan       recurrent epidemics EMRO
    "ve": "high",       # Venezuela      Americas
    "bo": "high",       # Bolivia        Americas
    "ec": "high",       # Ecuador        Americas
    "ni": "high",       # Nicaragua      Americas
    "cr": "high",       # Costa Rica     Americas
    "pa": "high",       # Panama         Americas
    "cu": "high",       # Cuba           Caribbean
    "ht": "high",       # Haiti          Caribbean
    "do": "high",       # Dominican Republic Caribbean
    "pr": "high",       # Puerto Rico    Caribbean
    "tt": "high",       # Trinidad & Tobago Caribbean
    "sg": "high",       # Singapore      Western Pacific
    "kh": "high",       # Cambodia       Western Pacific
    "la": "high",       # Laos           Western Pacific
    "tl": "high",       # Timor-Leste    Western Pacific
    "fj": "high",       # Fiji           Pacific Islands
    "mv": "high",       # Maldives       SEA
    # Moderate — sporadic/seasonal or emerging transmission
    "cn": "moderate",   # China          limited, primarily Guangdong province
    "au": "moderate",   # Australia      northern Queensland only
    "za": "moderate",   # South Africa   limited
    "ke": "moderate",   # Kenya          East Africa sporadic
    "tz": "moderate",   # Tanzania       East Africa sporadic
    "bf": "moderate",   # Burkina Faso   sharp rise 2024 Africa
    "ng": "moderate",   # Nigeria        underreported Africa
    "et": "moderate",   # Ethiopia       Africa
    "sd": "moderate",   # Sudan          EMRO
    "ye": "moderate",   # Yemen          EMRO recurrent
    "sa": "moderate",   # Saudi Arabia   EMRO limited
    "ao": "moderate",   # Angola         Africa
    "mz": "moderate",   # Mozambique     Africa
    "mg": "moderate",   # Madagascar     Africa
}

# -----------------------------
# REGIONAL CHOLERA R0 MAP
# Source: Our World in Data / WHO — number-of-reported-cholera-deaths.csv
# Metric: 5-year average annual deaths 2017-2021 (smooths single-year spikes)
#
# METHODOLOGICAL NOTE: Deaths are used here as a proxy for regional transmission
# intensity rather than incidence data. This is a limitation — deaths reflect both
# transmission intensity AND healthcare system capacity, so a country with poor
# treatment access could score high even if transmission chains are limited.
# For cholera specifically, the correlation between deaths and active waterborne
# transmission is strong: high-death countries (Nigeria, Yemen, DRC, Somalia)
# share compromised water/sanitation infrastructure that directly drives R0,
# not just poor treatment outcomes. The proxy is defensible but imperfect.
# TODO: replace with cholera incidence per 100,000 when a redistributable
# country-level dataset becomes available (Our World in Data incidence CSV
# returned 403 non-redistributable error as of June 2026).
# Tiers:
#   high     : >50 avg deaths/yr  (active endemic transmission)
#   moderate : 10-50 avg/yr       (recurrent outbreaks)
#   low      : 1-10 avg/yr        (sporadic, limited transmission)
#   default  : <1 avg/yr          (no endemic transmission)
# -----------------------------

CHOLERA_REGIONAL_R0 = {
    "high":     4.0,   # active endemic waterborne transmission
    "moderate": 2.5,   # recurrent seasonal outbreaks
    "low":      1.5,   # sporadic, limited transmission
    "default":  0.5,   # no endemic transmission
}

CHOLERA_COUNTRY_MAP = {
    # High burden — >50 avg deaths/yr 2017-2021
    "nga": "high",   # Nigeria       963.6 avg deaths/yr
    "yem": "high",   # Yemen         790.2
    "cod": "high",   # DR Congo      647.8
    "ssd": "high",   # South Sudan   353.0
    "som": "high",   # Somalia       226.4
    "ner": "high",   # Niger         122.0
    "eth": "high",   # Ethiopia       73.0
    "hti": "high",   # Haiti          67.7
    "ken": "high",   # Kenya          62.6
    "cmr": "high",   # Cameroon       61.8
    "tza": "high",   # Tanzania       61.0
    # Moderate burden — 10-50 avg deaths/yr
    "zmb": "moderate",  # Zambia      46.7
    "tcd": "moderate",  # Chad        45.0
    "zwe": "moderate",  # Zimbabwe    34.0
    "ago": "moderate",  # Angola      34.0
    "uga": "moderate",  # Uganda      22.6
    # Low burden — 1-10 avg deaths/yr
    "moz": "low",  # Mozambique  8.6
    "ben": "low",  # Benin       7.3
    "mwi": "low",  # Malawi      6.4
    "phl": "low",  # Philippines 5.8
    "mli": "low",  # Mali        4.0
    "sdn": "low",  # Sudan       3.7
    "tgo": "low",  # Togo        3.5
    "cog": "low",  # Congo       3.0
    "bdi": "low",  # Burundi     3.0
    "npl": "low",  # Nepal       2.3
    "dom": "low",  # Dominican Republic 1.7
    "ind": "low",  # India       1.5
    "mys": "low",  # Malaysia    1.0
}

# -----------------------------
# REGIONAL YELLOW FEVER R0 MAP
# Source: CDC Yellow Book 2024 + WHO IHR Country List (November 2022)
# Yellow fever has a sharp geographic boundary — endemic only in
# sub-Saharan Africa and tropical South America where Aedes/Haemagogus
# vectors and primate reservoirs coexist.
# Tiers based on CDC vaccination recommendation categories:
#   endemic      : vaccination recommended — active sylvatic/urban transmission risk
#   transitional : vaccination may be considered — lower but real risk
#   default      : no endemic transmission
# WHO AFRO 2024: 13 countries confirmed/probable YF cases in 2023:
#   Burkina Faso, Cameroon, CAR, Chad, Congo, Cote d'Ivoire, DRC,
#   Guinea, Niger, Nigeria, South Sudan, Togo, Uganda
# -----------------------------

YELLOW_FEVER_REGIONAL_R0 = {
    "endemic":      5.0,   # active sylvatic/urban transmission; vaccination recommended
    "transitional": 2.0,   # limited transmission risk; vaccination may be considered
    "default":      0.5,   # no endemic transmission
}

YELLOW_FEVER_COUNTRY_MAP = {
    # Endemic — Africa: CDC vaccination recommended
    "sen": "endemic",  # Senegal
    "gmb": "endemic",  # Gambia
    "gnb": "endemic",  # Guinea-Bissau
    "gin": "endemic",  # Guinea — confirmed cases 2023 WHO AFRO
    "sle": "endemic",  # Sierra Leone
    "lbr": "endemic",  # Liberia
    "civ": "endemic",  # Cote d'Ivoire — confirmed cases 2023 WHO AFRO
    "gha": "endemic",  # Ghana
    "ben": "endemic",  # Benin
    "bfa": "endemic",  # Burkina Faso — confirmed cases 2023 WHO AFRO
    "nga": "endemic",  # Nigeria — confirmed cases 2023 WHO AFRO
    "tgo": "endemic",  # Togo — confirmed cases 2023 WHO AFRO
    "cmr": "endemic",  # Cameroon — confirmed cases 2023 WHO AFRO
    "gnq": "endemic",  # Equatorial Guinea
    "stp": "endemic",  # Sao Tome and Principe
    "gab": "endemic",  # Gabon
    "cog": "endemic",  # Republic of Congo — confirmed cases 2023 WHO AFRO
    "cod": "endemic",  # DR Congo — confirmed cases 2023 WHO AFRO
    "bdi": "endemic",  # Burundi
    "ago": "endemic",  # Angola
    "uga": "endemic",  # Uganda — confirmed cases 2023 WHO AFRO
    "caf": "endemic",  # Central African Republic — confirmed cases 2023 WHO AFRO
    "ssd": "endemic",  # South Sudan — confirmed cases 2023 WHO AFRO
    # Endemic — South America: CDC vaccination recommended
    "guf": "endemic",  # French Guiana
    "sur": "endemic",  # Suriname
    "guy": "endemic",  # Guyana
    "pry": "endemic",  # Paraguay (except Asuncion)
    "bra": "endemic",  # Brazil (most of country)
    # Transitional — Africa: partial country risk
    "mrt": "transitional",  # Mauritania — partial
    "mli": "transitional",  # Mali — partial; confirmed cases 2023
    "ner": "transitional",  # Niger — partial; confirmed cases 2023
    "tcd": "transitional",  # Chad — partial; confirmed cases 2023
    "sdn": "transitional",  # Sudan — partial
    "eth": "transitional",  # Ethiopia — partial
    "ken": "transitional",  # Kenya — partial
    "tza": "transitional",  # Tanzania — generally not recommended
    "rwa": "transitional",  # Rwanda — generally not recommended
    # Transitional — South America: partial country risk
    "per": "transitional",  # Peru — east of Andes
    "ecu": "transitional",  # Ecuador — coastal parts
    "arg": "transitional",  # Argentina — north only
    "bol": "transitional",  # Bolivia — lowland departments
    "col": "transitional",  # Colombia — parts
    "ven": "transitional",  # Venezuela — parts
    "pan": "transitional",  # Panama — parts
}

# -----------------------------
# REGIONAL TYPHOID R0 MAP
# Sources:
# - Lancet Global Health SETA 2024: high burden in Burkina Faso, DRC, Ethiopia,
#   Ghana, Madagascar, Nigeria (>100 cases per 100,000 person-years)
# - PMC 2025 GBD analysis: burden concentrates in South Asia, Southeast Asia,
#   sub-Saharan Africa; Pakistan, India, Nepal highest paratyphoid burden
# - CDC MMWR 2023: high incidence in South-East Asian, Eastern Mediterranean,
#   and African regions; major outbreaks in Philippines, Zimbabwe, Pakistan
# Tiers based on incidence per 100,000 population:
#   very_high  : >100 cases/100k — active endemic fecal-oral transmission
#   high       : 10-100 cases/100k — significant burden, poor sanitation
#   moderate   : 1-10 cases/100k  — sporadic, improving WASH infrastructure
#   default    : <1 case/100k     — rare, travel-associated only
# -----------------------------

TYPHOID_REGIONAL_R0 = {
    "very_high": 6.0,   # >100 cases/100k; endemic fecal-oral transmission
    "high":      4.0,   # 10-100 cases/100k; frequent outbreaks
    "moderate":  2.0,   # sporadic; improving sanitation
    "default":   0.5,   # rare; high-income settings
}

TYPHOID_COUNTRY_MAP = {
    # Very high — Sub-Saharan Africa: SETA 2024 confirmed >100/100k
    "cod": "very_high",  # DR Congo    highest SETA burden
    "mdg": "very_high",  # Madagascar  highest SETA burden
    "bfa": "very_high",  # Burkina Faso SETA 2024
    "eth": "very_high",  # Ethiopia    SETA 2024
    "gha": "very_high",  # Ghana       SETA 2024; 943/100k in one study
    "nga": "very_high",  # Nigeria     SETA 2024
    "cmr": "very_high",  # Cameroon    68.75% prevalence in studies
    # Very high — South Asia: highest global paratyphoid + typhoid burden
    "pak": "very_high",  # Pakistan    #1 paratyphoid; major 2018-19 outbreak 14,894 cases
    "ind": "very_high",  # India       #2 paratyphoid; major burden per GBD 2025
    "npl": "very_high",  # Nepal       #3 paratyphoid; SEAP study
    "bgd": "very_high",  # Bangladesh  SEAP study high incidence
    # High — Southeast Asia + other Africa
    "phl": "high",       # Philippines 14,056 cases 2022 outbreak; CDC MMWR
    "zwe": "high",       # Zimbabwe    3 outbreaks 2017-2018; CDC MMWR
    "ken": "high",       # Kenya       endemic East Africa
    "tza": "high",       # Tanzania    endemic East Africa
    "uga": "high",       # Uganda      endemic East Africa
    "mwi": "high",       # Malawi      endemic Southern Africa
    "zmb": "high",       # Zambia      endemic Southern Africa
    "moz": "high",       # Mozambique  endemic Southern Africa
    "lka": "high",       # Sri Lanka   South Asia endemic
    "mmr": "high",       # Myanmar     Southeast Asia
    "vnm": "high",       # Vietnam     Southeast Asia
    "idn": "high",       # Indonesia   Southeast Asia
    # Moderate — improving but still present
    "chn": "moderate",   # China       2022 outbreak 23 cases; declining
    "mys": "moderate",   # Malaysia    Southeast Asia improving
    "tha": "moderate",   # Thailand    Southeast Asia improving
    "khm": "moderate",   # Cambodia    Southeast Asia
    "per": "moderate",   # Peru        Latin America
    "bol": "moderate",   # Bolivia     Latin America
    "hti": "moderate",   # Haiti       Caribbean
}

# -----------------------------
# REGIONAL HEPATITIS A R0 MAP
# Source: GBD 2021 acute hepatitis systematic analysis (PMC 2025) +
#         WHO endemicity classification (high/intermediate/low)
# Hepatitis A is strongly correlated with WASH (water, sanitation, hygiene)
# infrastructure. High-endemicity = poor sanitation, near-universal childhood
# exposure. Low-endemicity = good sanitation, adults susceptible and at risk.
# Tiers:
#   high     : high endemicity — poor WASH, endemic fecal-oral transmission
#   moderate : intermediate — improving sanitation, periodic outbreaks
#   low      : low endemicity — good WASH, travel-associated risk only
#   default  : very low — high-income, near-zero endemic transmission
# -----------------------------

HEPATITIS_A_REGIONAL_R0 = {
    "high":     6.0,   # poor WASH, endemic childhood infection
    "moderate": 3.5,   # improving sanitation, periodic outbreaks
    "low":      1.5,   # good sanitation, sporadic only
    "default":  0.5,   # high-income, near-zero transmission
}

HEPATITIS_A_COUNTRY_MAP = {
    # High endemicity — Sub-Saharan Africa + South Asia: poor WASH infrastructure
    "nga": "high",   # Nigeria
    "cod": "high",   # DR Congo
    "eth": "high",   # Ethiopia
    "uga": "high",   # Uganda
    "tza": "high",   # Tanzania
    "ken": "high",   # Kenya
    "moz": "high",   # Mozambique
    "mdg": "high",   # Madagascar
    "mwi": "high",   # Malawi
    "zmb": "high",   # Zambia
    "civ": "high",   # Cote d'Ivoire
    "gha": "high",   # Ghana
    "cmr": "high",   # Cameroon
    "ssd": "high",   # South Sudan
    "som": "high",   # Somalia
    "yem": "high",   # Yemen
    "afg": "high",   # Afghanistan
    "npl": "high",   # Nepal
    "bgd": "high",   # Bangladesh
    "pak": "high",   # Pakistan
    "hti": "high",   # Haiti
    # Moderate endemicity — improving but significant burden
    "ind": "moderate",  # India       improving but large population
    "idn": "moderate",  # Indonesia   Southeast Asia improving
    "phl": "moderate",  # Philippines Southeast Asia
    "mmr": "moderate",  # Myanmar     Southeast Asia
    "khm": "moderate",  # Cambodia    Southeast Asia
    "lao": "moderate",  # Laos        Southeast Asia
    "vnm": "moderate",  # Vietnam     improving rapidly
    "per": "moderate",  # Peru        Latin America
    "bol": "moderate",  # Bolivia     Latin America
    "gtm": "moderate",  # Guatemala   Central America
    "hn":  "moderate",  # Honduras    Central America
    "ni":  "moderate",  # Nicaragua   Central America
    "eg":  "moderate",  # Egypt       North Africa/MENA
    "ma":  "moderate",  # Morocco     North Africa
    "dz":  "moderate",  # Algeria     North Africa
    # Low endemicity — good sanitation, adults susceptible
    "br":  "low",    # Brazil      improving rapidly
    "mx":  "low",    # Mexico      improving
    "cn":  "low",    # China       improving rapidly
    "th":  "low",    # Thailand    improving
    "za":  "low",    # South Africa improving
    "tr":  "low",    # Turkey      intermediate-low
}

# -----------------------------
# REGIONAL RABIES R0 MAP
# Source: WHO SEARO (9 endemic SEA countries) + WOAH Asia report +
#         GBD 2021 rabies burden analysis (highest ASIR: Nepal 1.71,
#         Ethiopia 1.05, Malawi 0.77 per 100,000)
# NOTE: R0 here reflects dog bite exposure risk intensity, not traditional
# person-to-person R0 (rabies does not spread human-to-human).
# 95% of global rabies deaths occur in Africa and Asia (WHO/WOAH).
# India alone accounts for 36% of global rabies deaths (18,000-20,000/yr).
# South Asia accounts for ~45% of global rabies burden (WOAH 2023).
# Dog-mediated rabies eliminated in: Western Europe, US, Canada, Japan,
# Australia, most Latin American countries.
# Tiers:
#   very_high : highest dog-mediated rabies burden; limited PEP access
#   high      : significant endemic burden; improving but not controlled
#   moderate  : sporadic; some wildlife rabies or improving control
#   default   : dog-mediated rabies eliminated or never endemic
# -----------------------------

RABIES_REGIONAL_R0 = {
    "very_high": 3.0,   # highest dog bite + rabies burden; limited PEP
    "high":      2.0,   # significant endemic; improving control
    "moderate":  1.2,   # sporadic; wildlife or residual transmission
    "default":   0.1,   # eliminated or never endemic
}

RABIES_COUNTRY_MAP = {
    # Very high — South Asia: ~45% of global burden; WHO SEARO endemic list
    "ind": "very_high",  # India       36% of global deaths; 18-20k/yr
    "bgd": "very_high",  # Bangladesh  WHO SEARO endemic
    "npl": "very_high",  # Nepal       highest GBD 2021 ASIR (1.71/100k)
    "pak": "very_high",  # Pakistan    endemic; limited PEP access
    "mmr": "very_high",  # Myanmar     WHO SEARO endemic
    "lka": "very_high",  # Sri Lanka   WHO SEARO endemic
    "btn": "very_high",  # Bhutan      WHO SEARO endemic
    # Very high — Sub-Saharan Africa: 95% of non-Asian burden
    "eth": "very_high",  # Ethiopia    GBD 2021 ASIR 1.05/100k
    "mwi": "very_high",  # Malawi      GBD 2021 ASIR 0.77/100k
    "tza": "very_high",  # Tanzania    East Africa high burden
    "ken": "very_high",  # Kenya       East Africa high burden
    "uga": "very_high",  # Uganda      East Africa high burden
    "cod": "very_high",  # DR Congo    Central Africa high burden
    "nga": "very_high",  # Nigeria     West Africa high burden
    "cmr": "very_high",  # Cameroon    West Africa
    # High — Southeast Asia + rest of Africa
    "idn": "high",   # Indonesia   Southeast Asia endemic
    "phl": "high",   # Philippines Southeast Asia; WOAH Asia cluster 4
    "vnm": "high",   # Vietnam     Southeast Asia
    "khm": "high",   # Cambodia    Southeast Asia; WOAH Asia cluster 2
    "lao": "high",   # Laos        Southeast Asia; WOAH Asia cluster 2
    "tha": "high",   # Thailand    Southeast Asia; WOAH Asia cluster 4
    "chn": "high",   # China       significant rural burden
    "afg": "high",   # Afghanistan WOAH Eurasia cluster
    "moz": "high",   # Mozambique  Southern Africa
    "zmb": "high",   # Zambia      Southern Africa; SADC cluster
    "zwe": "high",   # Zimbabwe    Southern Africa; SADC cluster
    "zaf": "high",   # South Africa endemic in KZN province
    "gha": "high",   # Ghana       West Africa
    "ssd": "high",   # South Sudan high burden
    # Moderate — sporadic or improving control
    "mex": "moderate",  # Mexico      dog rabies mostly controlled; wildlife
    "col": "moderate",  # Colombia    improving
    "per": "moderate",  # Peru        improving
    "bra": "moderate",  # Brazil      dog-mediated mostly controlled; bat rabies
    "rus": "moderate",  # Russia      wildlife rabies (fox, raccoon dog)
    "tur": "moderate",  # Turkey      improving; some endemic areas
    "irn": "moderate",  # Iran        some endemic areas
}

# -----------------------------
# REGIONAL ZIKA R0 MAP
# Source: WHO Zika Epidemiology Update 2024 + PAHO 2026 + PMC review 2025
# As of May 2024, ongoing transmission in 92 countries (WHO).
# Highest burden: Americas (39% global seroprevalence) — Brazil leads with
# 1,801 confirmed cases in 2024 per PAHO/WHO.
# PAHO confirmed local transmission in 52 countries/territories in Americas.
# Transmission also persists in Southeast Asia, Western Pacific, and Africa
# at lower levels (PMC 2025 review).
# Tiers:
#   high     : Americas — highest ongoing transmission + seroprevalence
#   moderate : Southeast Asia, Western Pacific, parts of Africa — documented
#              autochthonous transmission but lower intensity
#   low      : sporadic or recently detected transmission
#   default  : no documented autochthonous transmission
# -----------------------------

ZIKA_REGIONAL_R0 = {
    "high":     4.0,   # Americas; highest ongoing transmission
    "moderate": 2.0,   # SEA/Pacific/Africa; documented but lower intensity
    "low":      1.0,   # sporadic or emerging transmission
    "default":  0.3,   # no autochthonous transmission
}

ZIKA_COUNTRY_MAP = {
    # High — Americas: PAHO confirmed local transmission, Brazil leads 2024-2026
    "bra": "high",   # Brazil      1,801 confirmed cases 2024; ongoing
    "col": "high",   # Colombia    Americas endemic
    "ven": "high",   # Venezuela   Americas endemic
    "per": "high",   # Peru        Americas endemic
    "ecu": "high",   # Ecuador     Americas endemic
    "bol": "high",   # Bolivia     Americas endemic
    "guy": "high",   # Guyana      Americas endemic
    "sur": "high",   # Suriname    Americas endemic
    "guf": "high",   # French Guiana Americas endemic
    "pry": "high",   # Paraguay    Americas endemic
    "arg": "high",   # Argentina   Americas endemic (southern outbreaks)
    "mex": "high",   # Mexico      Americas endemic
    "gtm": "high",   # Guatemala   Central America
    "hnd": "high",   # Honduras    Central America
    "ni":  "high",   # Nicaragua   Central America
    "cri": "high",   # Costa Rica  Central America
    "pan": "high",   # Panama      Central America
    "hti": "high",   # Haiti       Caribbean
    "dom": "high",   # Dominican Republic Caribbean
    "pri": "high",   # Puerto Rico Caribbean
    "slv": "high",   # El Salvador Central America
    # Moderate — Southeast Asia + Western Pacific + Africa
    "idn": "moderate",  # Indonesia   Southeast Asia documented
    "phl": "moderate",  # Philippines Southeast Asia documented
    "tha": "moderate",  # Thailand    Southeast Asia documented
    "vnm": "moderate",  # Vietnam     Southeast Asia documented
    "mmr": "moderate",  # Myanmar     Southeast Asia documented
    "khm": "moderate",  # Cambodia    Southeast Asia documented
    "sgp": "moderate",  # Singapore   Western Pacific documented
    "mys": "moderate",  # Malaysia    Western Pacific
    "fji": "moderate",  # Fiji        Pacific Islands
    "png": "moderate",  # Papua New Guinea Pacific
    "sen": "moderate",  # Senegal     Africa; detected 2023 (PMC review)
    "gnb": "moderate",  # Guinea      Africa documented
    "mli": "moderate",  # Mali        Africa; detected 2023 (PMC review)
    "lka": "moderate",  # Sri Lanka   South Asia; added to WHO list 2023
    # Low — sporadic or recently detected
    "sey": "low",    # Seychelles  new detection 2024 (WHO update)
    "mdg": "low",    # Madagascar  5 confirmed cases late 2025 (WHO update)
    "civ": "low",    # Cote d'Ivoire Africa sporadic
    "cmr": "low",    # Cameroon    Africa sporadic
}

# -----------------------------
# REGIONAL CHIKUNGUNYA R0 MAP
# Source: JOGH 2026 global burden study (2004-2024) + Nature Medicine 2025 +
#         ECDC monthly surveillance 2026
# 2024 burden: Americas 43.9/100k (431,305 cases), SEA 14.3/100k (258,854 cases)
# India alone: 192,343 cases 2024; 200,064 in 2023 (National Vector Borne Disease Control)
# Americas and South-East Asia now bear heaviest burden; Africa likely underreported
# Tiers:
#   very_high : Americas — 43.9/100k incidence 2024; highest documented burden
#   high      : South-East Asia — 14.3/100k; India, Thailand, Myanmar, Pakistan
#   moderate  : Eastern Mediterranean + parts of Africa; 1.43/100k
#   default   : Europe, Western Pacific, North America — sporadic/travel-associated
# -----------------------------

CHIKUNGUNYA_REGIONAL_R0 = {
    "very_high": 5.0,   # Americas; highest 2024 burden
    "high":      3.5,   # South-East Asia; major ongoing transmission
    "moderate":  2.0,   # Eastern Mediterranean + underreported Africa
    "default":   0.5,   # sporadic/travel-associated only
}

CHIKUNGUNYA_COUNTRY_MAP = {
    # Very high — Americas: JOGH 2026; Brazil, Paraguay, Bolivia, Colombia top cases
    "bra": "very_high",  # Brazil      largest Americas burden
    "pry": "very_high",  # Paraguay    2nd highest Americas 2023
    "col": "very_high",  # Colombia    Americas endemic
    "bol": "very_high",  # Bolivia     Americas endemic
    "ven": "very_high",  # Venezuela   Americas endemic
    "per": "very_high",  # Peru        Americas endemic
    "gtm": "very_high",  # Guatemala   Central America
    "hnd": "very_high",  # Honduras    Central America
    "slv": "very_high",  # El Salvador Central America
    "cri": "very_high",  # Costa Rica  Central America
    "pan": "very_high",  # Panama      Central America
    "mex": "very_high",  # Mexico      Americas endemic
    "dom": "very_high",  # Dominican Republic Caribbean
    "hti": "very_high",  # Haiti       Caribbean
    "cub": "very_high",  # Cuba        Caribbean; 2026 ECDC
    "guy": "very_high",  # Guyana      Caribbean/Americas 2026
    "lca": "very_high",  # Saint Lucia Caribbean; 2026 ECDC
    # High — South-East Asia: 14.3/100k 2024; India drives most of regional burden
    "ind": "high",   # India       192,343 cases 2024; Level 2 CDC advisory
    "pak": "high",   # Pakistan    SEA 2026 ECDC; only country reporting in 2026
    "tha": "high",   # Thailand    SEA endemic; ECDC 2025
    "mmr": "high",   # Myanmar     SEA endemic
    "sgp": "high",   # Singapore   SEA endemic; ECDC 2025
    "tls": "high",   # Timor-Leste SEA; 195 cases 2023 ECDC
    "mys": "high",   # Malaysia    1 case 2023 but SEA endemic
    "idn": "high",   # Indonesia   SEA endemic
    "lka": "high",   # Sri Lanka   South Asia outbreaks documented
    "bgd": "high",   # Bangladesh  South Asia documented
    "npl": "high",   # Nepal       South Asia documented
    # Moderate — Eastern Mediterranean + Africa (underreported)
    "yem": "moderate",   # Yemen       EMRO documented
    "sdn": "moderate",   # Sudan       EMRO/Africa
    "nga": "moderate",   # Nigeria     West Africa; underreported
    "ken": "moderate",   # Kenya       East Africa
    "tza": "moderate",   # Tanzania    East Africa
    "cod": "moderate",   # DRC         Central Africa
    "cmr": "moderate",   # Cameroon    West/Central Africa
    "mrt": "moderate",   # Mauritius   Indian Ocean; 2026 ECDC
}

# -----------------------------
# REGIONAL WEST NILE R0 MAP
# Source: WHO Europe Q&A 2024 + ECDC 2023 transmission season report +
#         PMC Mediterranean Basin systematic review 2025
# WNV commonly found in Africa, Middle East, North America, West Asia (WHO Europe)
# 2023 EU season: 709 locally acquired cases in 9 countries; Italy (336), Greece (162),
#         Romania (103) highest; 13 countries reporting in 2024
# Western Asia seroprevalence 10.6% (moderate transmission intensity)
# North America: endemic since 1999; 2,000-5,000 cases/yr in US
# Tiers:
#   high     : North America — endemic since 1999; consistent annual burden
#   moderate : Mediterranean + Western Asia — annual seasonal outbreaks
#   low      : Sub-Saharan Africa + rest of Asia — documented but limited human cases
#   default  : no documented endemic transmission
# -----------------------------

WEST_NILE_REGIONAL_R0 = {
    "high":     4.0,   # North America; consistent endemic annual transmission
    "moderate": 2.5,   # Mediterranean + Western Asia; seasonal outbreaks
    "low":      1.5,   # Africa + rest of Asia; documented but limited
    "default":  0.3,   # no endemic transmission
}

WEST_NILE_COUNTRY_MAP = {
    # High — North America: endemic since 1999; consistent 2,000-5,000 US cases/yr
    "us":  "high",   # United States  endemic since 1999; highest global burden
    "ca":  "high",   # Canada         North America endemic
    # Moderate — Mediterranean Basin: annual seasonal outbreaks
    "ita": "moderate",  # Italy         336 cases 2023; highest EU burden
    "grc": "moderate",  # Greece        162 cases 2023; high neuroinvasive rate 70.3%
    "rou": "moderate",  # Romania       103 cases 2023
    "fra": "moderate",  # France        43 cases 2023
    "hun": "moderate",  # Hungary       29 cases 2023
    "esp": "moderate",  # Spain         19 cases 2023
    "hrv": "moderate",  # Croatia       6 cases 2023
    "cyp": "moderate",  # Cyprus        5 cases 2023
    "deu": "moderate",  # Germany       6 cases 2023
    "srb": "moderate",  # Serbia        neighbouring; 2023 season
    "tur": "moderate",  # Turkey        neighbouring; 2023 season
    "alb": "moderate",  # Albania       2024 season reporting
    "mkd": "moderate",  # North Macedonia 2024 season reporting
    # Moderate — Western Asia: seroprevalence 10.6% (PMC review 2025)
    "isr": "moderate",  # Israel        hundreds of historical cases; endemic
    "egy": "moderate",  # Egypt         historical endemic; North Africa
    "dza": "moderate",  # Algeria       1994 outbreak; North Africa endemic
    "mar": "moderate",  # Morocco       North Africa endemic
    # Low — Sub-Saharan Africa + rest of Asia: documented historically
    "zaf": "low",    # South Africa   hundreds of historical cases; 1974 epidemic
    "nga": "low",    # Nigeria        historical outbreaks
    "eth": "low",    # Ethiopia       historical documented
    "ind": "low",    # India          travel-associated cases documented
    "rus": "low",    # Russia         historical outbreaks 1990s
}

# Source: WHO fact sheets + JOGH outbreak analysis 1996-2023
# CFR = case fatality rate (0-1), R0 = basic reproduction number
# -----------------------------

DISEASE_PROFILES = {
    "ebola":           {"cfr": 0.90, "r0": 2.0,  "transmission": "contact",   "vaccine": False},
    "marburg":         {"cfr": 0.88, "r0": 2.0,  "transmission": "contact",   "vaccine": False},
    "cholera": {
        "cfr": 0.50,        # untreated worst case; drops to <0.01 with treatment
                            # source: WHO fact sheet
        "r0": 3.0,          # placeholder; overridden per region by get_r0_for_region()
                            # Regional values in CHOLERA_REGIONAL_R0
                            # source: Our World in Data / WHO reported deaths 2017-2021
        "transmission": "waterborne",
        "vaccine": True,
    },
    "measles":         {"cfr": 0.15, "r0": 15.0, "transmission": "airborne",  "vaccine": True},
    "polio":           {
        "cfr": 0.10,        # reflects paralytic polio cases only; overall infection CFR ~0.001
                            # source: WHO eradication program data
        "r0": 6.0,
        "transmission": "fecal-oral",
        "vaccine": True,
    },
    "dengue": {
        "cfr": 0.025,       # WHO 2024: global CFR 0.07%; severe dengue up to 5%
                            # source: WHO fact sheet + ScienceDirect 2024 global analysis
        "r0": 3.0,          # placeholder; overridden per region by get_r0_for_region()
                            # Regional values in DENGUE_REGIONAL_R0
                            # source: WHO 2024 global dengue surveillance + CDC Areas with Risk
        "transmission": "vector",
        "vaccine": False,   # NOTE: Dengvaxia/Qdenga available but not widely deployed
    },
    "avian influenza": {"cfr": 0.60, "r0": 1.2,  "transmission": "airborne",  "vaccine": False},
    "lassa":           {
        "cfr": 0.30,        # reflects hospitalized cases; population-level CFR ~0.01
                            # source: WHO Lassa fever fact sheet
        "r0": 2.0,
        "transmission": "contact",
        "vaccine": False,
    },
    "yellow fever": {
        "cfr": 0.40,        # severe cases 20-50%; source: WHO fact sheet
                            # 2023 WHO AFRO preliminary CFR: 11%
        "r0": 2.5,          # placeholder; overridden per region by get_r0_for_region()
                            # Regional values in YELLOW_FEVER_REGIONAL_R0
                            # source: CDC Yellow Book 2024 + WHO IHR country list 2022
        "transmission": "vector",
        "vaccine": True,
    },
    "mpox": {
        "cfr": 0.10,        # reflects clade I (DRC); clade II ~0.01
        "r0": 2.5,
        "transmission": "contact",
        "vaccine": True,
        # NOTE: clade I (Central Africa) significantly more severe than clade II
        # Update cfr if outbreak strain is confirmed clade II
    },
    "malaria": {
        "cfr": 0.20,        # sub-Saharan Africa untreated severe malaria; source: WHO World Malaria Report
        "r0": 18.0,         # placeholder; overridden per region by get_r0_for_region()
                            # R0 not comparable to direct-transmission diseases (Ross-Macdonald framework)
                            # Regional values in MALARIA_REGIONAL_R0, sourced from WHO/World Bank 2024
        "transmission": "vector",
        "vaccine": True,
    },
    "typhoid": {
        "cfr": 0.15,        # untreated; drops to <0.01 with antibiotics; source: WHO fact sheet
        "r0": 4.0,          # placeholder; overridden per region by get_r0_for_region()
                            # Regional values in TYPHOID_REGIONAL_R0
                            # source: Lancet Global Health SETA 2024 + CDC MMWR 2023
        "transmission": "fecal-oral",
        "vaccine": True,
    },
    "zika": {
        "cfr": 0.01,        # NOTE: primary danger is teratogenic (microcephaly); direct mortality low
        "r0": 3.0,          # placeholder; overridden per region by get_r0_for_region()
                            # Regional values in ZIKA_REGIONAL_R0
                            # source: WHO Zika Epidemiology Update 2024 + PAHO 2026
        "transmission": "vector",
        "vaccine": False,
    },
    "chikungunya": {
        "cfr": 0.01,        # source: WHO fact sheet; ~1/1000 cases fatal, mainly neonates/elderly
        "r0": 3.5,          # placeholder; overridden per region by get_r0_for_region()
                            # Regional values in CHIKUNGUNYA_REGIONAL_R0
                            # source: JOGH 2026 global burden study + Nature Medicine 2025
        "transmission": "vector",
        "vaccine": False,   # NOTE: IXCHIQ (Valneva) and VIMKUNYA (Bavarian Nordic) approved
                            # 2024 but not yet widely deployed
    },
    "covid": {
        "cfr": 0.01,        # reflects omicron-era variants (2023-present)
                            # original strain CFR was ~0.02-0.03
        "r0": 3.5,
        "transmission": "airborne",
        "vaccine": True,
        # NOTE: monitor CDC COVID Nowcast for dominant variant shifts
        # endpoint: cdc.gov/covid/vaccines/effectiveness-research/nowcast.html
    },
    "influenza": {
        "cfr": 0.001,       # seasonal H3N2 2024-25, source: CDC FluView
        "r0": 2.5,          # seasonal average; pandemic strains can reach 3.5+
        "transmission": "airborne",
        "vaccine": True,
        # TODO: wire to CDC FluView API for real-time dominant strain
        # endpoint: https://www.cdc.gov/flu/weekly/fluviewinteractive.htm
    },
    "rsv": {
        "cfr": 0.001,       # reflects healthy adult population; up to 0.17 in immunocompromised/premature infants
                            # source: CDC RSV surveillance
        "r0": 3.0,
        "transmission": "airborne",
        "vaccine": True,
    },
    "west nile": {
        "cfr": 0.09,        # neuroinvasive disease CFR; overall infection CFR ~0.001
                            # source: CDC West Nile virus surveillance
        "r0": 3.0,          # placeholder; overridden per region by get_r0_for_region()
                            # Regional values in WEST_NILE_REGIONAL_R0
                            # source: WHO Europe Q&A 2024 + ECDC 2023 season report
        "transmission": "vector",
        "vaccine": False,
    },
    "hepatitis a": {
        "cfr": 0.01,        # source: WHO Hepatitis A fact sheet
        "r0": 5.0,          # placeholder; overridden per region by get_r0_for_region()
                            # Regional values in HEPATITIS_A_REGIONAL_R0
                            # source: GBD 2021 acute hepatitis analysis; WHO endemicity map
        "transmission": "fecal-oral",
        "vaccine": True,
    },
    "rabies": {
        "cfr": 1.00,        # effectively 100% once symptomatic; source: WHO fact sheet
        "r0": 1.0,          # placeholder; overridden per region by get_r0_for_region()
                            # Regional values in RABIES_REGIONAL_R0
                            # reflects dog bite exposure risk, not traditional R0
                            # source: WHO SEARO + WOAH + GBD 2021 burden data
        "transmission": "contact",
        "vaccine": True,
        # NOTE: vaccine is post-exposure prophylaxis, not pre-exposure population immunity
    },
}

# Transmission route multipliers
# Airborne diseases spread faster and are harder to contain
TRANSMISSION_MULTIPLIER = {
    "airborne":   1.3,
    "contact":    1.1,
    "vector":     1.0,
    "waterborne": 0.95,
    "fecal-oral": 0.9,
}

# Variable weights for composite score
# CFR weighted heaviest — most direct measure of severity
WEIGHTS = {
    "cfr":          0.45,
    "r0":           0.30,
    "transmission": 0.15,
    "vaccine":      0.10,
}

# Keyword list for disease detection
DISEASE_KEYWORDS = list(DISEASE_PROFILES.keys())

# Tier thresholds
TIER_THRESHOLDS = {
    "emergency":  60,
    "seek_care":  30,
    "monitor":     0,
}

# -----------------------------
# FILE HELPERS
# -----------------------------

def load_json_file(path, default_value):
    import json
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_value

# -----------------------------
# FLUVIEW STRAIN DETECTION
# Source: Delphi Epidata API (Carnegie Mellon / CDC partnership)
# Endpoint: api.delphi.cmu.edu/epidata/fluview
# Fetches most recent national public health lab subtype data to determine
# whether H3N2, H1N1, or influenza B is currently dominant.
# Updates influenza profile CFR and R0 dynamically based on dominant strain.
# Cached 24 hours — flu strain shifts weekly but not hourly.
#
# Strain profiles sourced from:
# - H3N2 (seasonal): CFR 0.001, R0 2.5 — CDC FluView historical averages
# - H1N1 (seasonal): CFR 0.001, R0 2.5 — similar severity to H3N2
# - H3N2 (severe):  CFR 0.002, R0 3.0 — used when H3N2 >70% of subtyped specimens
#   (2025-26 season: H3N2 at 91.2% — The Hill/CDC reporting Jan 2026)
# - Influenza B:    CFR 0.0005, R0 2.0 — generally milder than A subtypes
# - Pandemic:       CFR 0.010+, R0 3.5+ — flagged if novel A subtype detected
# -----------------------------

FLUVIEW_STRAIN_PROFILES = {
    "H3N2_dominant": {
        "cfr": 0.002,   # H3N2 dominant seasons trend more severe
        "r0":  3.0,
        "label": "H3N2 dominant",
        "note": "H3N2 is the predominant circulating strain this season."
    },
    "H1N1_dominant": {
        "cfr": 0.001,
        "r0":  2.5,
        "label": "H1N1 dominant",
        "note": "H1N1 (pdm09) is the predominant circulating strain this season."
    },
    "B_dominant": {
        "cfr": 0.0005,
        "r0":  2.0,
        "label": "Influenza B dominant",
        "note": "Influenza B (Victoria lineage) is the predominant circulating strain."
    },
    "mixed": {
        "cfr": 0.001,
        "r0":  2.5,
        "label": "Mixed A/B circulation",
        "note": "Multiple influenza strains are co-circulating this season."
    },
    "off_season": {
        "cfr": 0.001,
        "r0":  1.5,
        "label": "Off-season (low activity)",
        "note": "Influenza activity is low — off-season baseline values used."
    },
    "fallback": {
        "cfr": 0.001,
        "r0":  2.5,
        "label": "Seasonal average (data unavailable)",
        "note": "Using seasonal average — live strain data temporarily unavailable."
    },
}

def get_fluview_strain():
    """
    Fetch the current dominant influenza strain from the Delphi Epidata API.
    Returns a FLUVIEW_STRAIN_PROFILES key string.
    Caches result for 24 hours to avoid hammering the API.

    API: https://api.delphi.cmu.edu/epidata/fluview/
    Fetches the 4 most recent epiweeks of national public health lab data
    and determines dominant strain from cumulative H1N1 vs H3N2 vs B counts.
    """
    global FLUVIEW_CACHE
    now = time.time()

    # Return cached result if still fresh
    if FLUVIEW_CACHE["data"] and (now - FLUVIEW_CACHE["ts"] < FLUVIEW_CACHE_TTL_SECONDS):
        return FLUVIEW_CACHE["data"]

    try:
        # Calculate current epiweek (YYYYWW format)
        today = datetime.utcnow()
        # Epiweek: CDC week starting Sunday; approximate with isocalendar
        iso_year, iso_week, _ = today.isocalendar()
        current_epiweek = iso_year * 100 + iso_week
        # Request last 4 weeks to get enough subtyped specimens
        start_epiweek   = current_epiweek - 4

        url = "https://api.delphi.cmu.edu/epidata/fluview/"
        params = {
            "regions": "nat",
            "epiweeks": f"{start_epiweek}-{current_epiweek}",
        }
        headers = {"User-Agent": USER_AGENT}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        payload = resp.json()

        if payload.get("result") != 1 or not payload.get("epidata"):
            raise ValueError("No epidata returned")

        # Sum H1N1, H3N2, and B across returned weeks (national level)
        total_h1n1 = 0
        total_h3n2 = 0
        total_b    = 0

        for row in payload["epidata"]:
            # Field names from Delphi API: num_ili, num_patients, wili
            # Subtype breakdown is not in ILINet — this endpoint gives ILI %
            # We need the WHO_NREVSS endpoint for subtype data
            # Fall through to use wili (weighted ILI) to detect off-season
            pass

        # ILINet doesn't have subtype breakdown — use wili to detect off-season
        # then fall back to WHO_NREVSS for subtype data via the public API
        wili_values = [row.get("wili", 0) for row in payload["epidata"] if row.get("wili")]
        avg_wili = sum(wili_values) / len(wili_values) if wili_values else 0

        if avg_wili < 2.0:
            # Below 2% weighted ILI = off-season baseline
            strain_key = "off_season"
        else:
            # Season is active — try WHO_NREVSS clinical labs for subtype breakdown
            nrevss_url = "https://api.delphi.cmu.edu/epidata/fluview_clinical/"
            nrevss_params = {
                "regions": "nat",
                "epiweeks": f"{start_epiweek}-{current_epiweek}",
            }
            nrevss_resp = requests.get(nrevss_url, params=nrevss_params, headers=headers, timeout=10)
            nrevss_resp.raise_for_status()
            nrevss_payload = nrevss_resp.json()

            if nrevss_payload.get("result") == 1 and nrevss_payload.get("epidata"):
                for row in nrevss_payload["epidata"]:
                    total_h1n1 += row.get("total_a_h1n1_pdm09", 0) or 0
                    total_h3n2 += row.get("total_a_h3", 0) or 0
                    total_b    += row.get("total_b", 0) or 0

            total_a = total_h1n1 + total_h3n2
            total   = total_a + total_b

            if total < 10:
                # Not enough subtyped specimens — use mixed/seasonal average
                strain_key = "mixed"
            elif total_b > (total_a * 1.5):
                strain_key = "B_dominant"
            elif total_h3n2 > (total_h1n1 * 1.5):
                # H3N2 dominant — check if severely dominant (>70%)
                h3n2_pct = total_h3n2 / total if total > 0 else 0
                strain_key = "H3N2_dominant"
                # Bump CFR slightly if extremely dominant (like 2025-26 at 91%)
                if h3n2_pct > 0.70:
                    FLUVIEW_STRAIN_PROFILES["H3N2_dominant"]["cfr"] = 0.002
                    FLUVIEW_STRAIN_PROFILES["H3N2_dominant"]["r0"]  = 3.0
                else:
                    FLUVIEW_STRAIN_PROFILES["H3N2_dominant"]["cfr"] = 0.0015
                    FLUVIEW_STRAIN_PROFILES["H3N2_dominant"]["r0"]  = 2.8
            elif total_h1n1 > (total_h3n2 * 1.5):
                strain_key = "H1N1_dominant"
            else:
                strain_key = "mixed"

        FLUVIEW_CACHE = {"ts": now, "data": strain_key}
        return strain_key

    except Exception as e:
        # Any failure: log it, return fallback, cache fallback briefly (1 hour)
        print(f"[FluView] fetch failed: {e} — using fallback")
        FLUVIEW_CACHE = {"ts": now - FLUVIEW_CACHE_TTL_SECONDS + 3600, "data": "fallback"}
        return "fallback"

# -----------------------------
# RSS HELPERS
# -----------------------------

def parse_rss(url, max_items=15, days_back=90):
    feed = feedparser.parse(url)
    items = []
    cutoff = datetime.utcnow() - timedelta(days=days_back)

    for entry in feed.entries:
        published_dt = None
        if getattr(entry, "published_parsed", None):
            published_dt = datetime(*entry.published_parsed[:6])
        elif getattr(entry, "updated_parsed", None):
            published_dt = datetime(*entry.updated_parsed[:6])

        if published_dt and published_dt < cutoff:
            continue

        title = getattr(entry, "title", "Untitled")
        link = getattr(entry, "link", None)
        summary = getattr(entry, "summary", "")[:700]

        items.append({
            "title": title,
            "link": link,
            "published": published_dt.isoformat() if published_dt else None,
            "summary": summary,
            "source": url,
        })

        if len(items) >= max_items:
            break

    return items

def get_cached_rss(cache_key, url):
    cached = RSS_CACHE.get(cache_key)
    now = time.time()

    if cached and (now - cached["ts"] < RSS_CACHE_TTL_SECONDS):
        return cached["items"]

    items = parse_rss(url, max_items=15, days_back=90)
    RSS_CACHE[cache_key] = {"ts": now, "items": items}
    return items

# -----------------------------
# GEO HELPERS
# -----------------------------

def reverse_geocode(lat, lon):
    key = f"{round(lat, 4)},{round(lon, 4)}"
    cached = GEOCODE_CACHE.get(key)
    now = time.time()

    if cached and (now - cached["ts"] < GEOCODE_CACHE_TTL_SECONDS):
        return cached["data"]

    url = "https://nominatim.openstreetmap.org/reverse"
    params = {"format": "jsonv2", "lat": lat, "lon": lon}
    headers = {"User-Agent": USER_AGENT}

    resp = requests.get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    address = data.get("address", {}) or {}
    normalized = {
        "display_name": data.get("display_name"),
        "country": address.get("country"),
        "country_code": address.get("country_code"),
        "state": address.get("state"),
        "county": address.get("county"),
        "city": address.get("city") or address.get("town") or address.get("village"),
        "postcode": address.get("postcode"),
        "lat": lat,
        "lon": lon,
    }

    GEOCODE_CACHE[key] = {"ts": now, "data": normalized}
    return normalized

# -----------------------------
# DISEASE DETECTION
# -----------------------------

def extract_diseases_from_text(item):
    """Extract recognized disease names from alert title and summary."""
    text = f"{item.get('title','')} {item.get('summary','')}".lower()
    return [disease for disease in DISEASE_KEYWORDS if disease in text]

# -----------------------------
# LOCATION MATCHING
# -----------------------------

def alert_matches_user_location(item, loc):
    """
    Check whether a WHO/CDC alert is relevant to the user's location.
    Uses both text matching AND a country-code → alias map so that
    'Democratic Republic of the Congo', 'DRC', 'Congo', and 'Kinshasa'
    all correctly match when the user searches Kinshasa, DRC.
    """
    if not loc:
        return True

    text = f"{item.get('title','')} {item.get('summary','')}".lower()

    # Build the set of terms to search for from the location object
    cc = (loc.get("country_code") or "").lower()

    location_terms = [
        loc.get("country"),
        loc.get("state"),
        loc.get("county"),
        loc.get("city"),
    ]

    # Country code → list of text aliases that WHO/CDC use in alerts
    COUNTRY_ALIASES = {
        "cd": ["democratic republic of the congo", "drc", "dr congo", "congo-kinshasa",
               "kinshasa", "ituri", "north kivu", "south kivu", "katanga", "kasai"],
        "ug": ["uganda", "kampala", "bundibugyo"],
        "ng": ["nigeria", "lagos", "abuja", "kano"],
        "et": ["ethiopia", "addis ababa"],
        "so": ["somalia", "mogadishu"],
        "ss": ["south sudan", "juba"],
        "ye": ["yemen", "sanaa", "aden"],
        "pk": ["pakistan", "islamabad", "karachi", "lahore"],
        "in": ["india", "new delhi", "mumbai", "delhi"],
        "bd": ["bangladesh", "dhaka"],
        "br": ["brazil", "sao paulo", "rio de janeiro", "brasilia"],
        "cn": ["china", "beijing", "shanghai", "wuhan"],
        "ke": ["kenya", "nairobi", "mombasa"],
        "gh": ["ghana", "accra"],
        "cm": ["cameroon", "yaounde", "douala"],
        "za": ["south africa", "johannesburg", "cape town", "pretoria"],
        "mx": ["mexico", "mexico city"],
        "co": ["colombia", "bogota"],
        "pe": ["peru", "lima"],
        "ph": ["philippines", "manila"],
        "id": ["indonesia", "jakarta"],
        "th": ["thailand", "bangkok"],
        "vn": ["vietnam", "hanoi", "ho chi minh"],
        "mm": ["myanmar", "burma", "yangon"],
        "af": ["afghanistan", "kabul"],
        "ht": ["haiti", "port-au-prince"],
        "sd": ["sudan", "khartoum"],
        "sn": ["senegal", "dakar"],
        "ml": ["mali", "bamako"],
        "bf": ["burkina faso", "ouagadougou"],
        "gn": ["guinea", "conakry"],
        "lr": ["liberia", "monrovia"],
        "sl": ["sierra leone", "freetown"],
        "cg": ["republic of congo", "brazzaville"],
        "ao": ["angola", "luanda"],
        "mz": ["mozambique", "maputo"],
        "mw": ["malawi", "lilongwe"],
        "zm": ["zambia", "lusaka"],
        "zw": ["zimbabwe", "harare"],
        "tz": ["tanzania", "dar es salaam", "dodoma"],
        "rw": ["rwanda", "kigali"],
        "bi": ["burundi", "bujumbura"],
        "us": ["united states", "usa", "u.s.", "america"],
        "gb": ["united kingdom", "uk", "england", "britain", "london"],
        "fr": ["france", "paris"],
        "de": ["germany", "berlin"],
        "it": ["italy", "rome"],
        "es": ["spain", "madrid"],
    }

    # Add country-code aliases to search terms
    if cc in COUNTRY_ALIASES:
        location_terms.extend(COUNTRY_ALIASES[cc])

    for term in location_terms:
        if term and term.lower() in text:
            return True

    return False

# -----------------------------
# REGIONAL R0 LOOKUP
# -----------------------------

def get_r0_for_region(disease_name, country_code):
    """
    Return the appropriate R0 for a disease given the user's country.
    For most diseases, R0 is flat (not region-dependent).
    For malaria and dengue, R0 varies significantly by region.

    Regional sources:
    - Malaria: WHO Global Health Observatory via World Bank 2024
    - Dengue:  WHO 2024 Global Dengue Surveillance + CDC Areas with Risk
    """
    profile = DISEASE_PROFILES.get(disease_name)
    if not profile:
        return 1.0

    cc = (country_code or "").lower()

    if disease_name == "malaria":
        region = COUNTRY_REGION_MAP.get(cc, "default")
        return MALARIA_REGIONAL_R0.get(region, MALARIA_REGIONAL_R0["default"])

    if disease_name == "dengue":
        tier = DENGUE_COUNTRY_MAP.get(cc, "default")
        return DENGUE_REGIONAL_R0.get(tier, DENGUE_REGIONAL_R0["default"])

    if disease_name == "cholera":
        tier = CHOLERA_COUNTRY_MAP.get(cc, "default")
        return CHOLERA_REGIONAL_R0.get(tier, CHOLERA_REGIONAL_R0["default"])

    if disease_name == "yellow fever":
        tier = YELLOW_FEVER_COUNTRY_MAP.get(cc, "default")
        return YELLOW_FEVER_REGIONAL_R0.get(tier, YELLOW_FEVER_REGIONAL_R0["default"])

    if disease_name == "typhoid":
        tier = TYPHOID_COUNTRY_MAP.get(cc, "default")
        return TYPHOID_REGIONAL_R0.get(tier, TYPHOID_REGIONAL_R0["default"])

    if disease_name == "hepatitis a":
        tier = HEPATITIS_A_COUNTRY_MAP.get(cc, "default")
        return HEPATITIS_A_REGIONAL_R0.get(tier, HEPATITIS_A_REGIONAL_R0["default"])

    if disease_name == "rabies":
        tier = RABIES_COUNTRY_MAP.get(cc, "default")
        return RABIES_REGIONAL_R0.get(tier, RABIES_REGIONAL_R0["default"])

    if disease_name == "zika":
        tier = ZIKA_COUNTRY_MAP.get(cc, "default")
        return ZIKA_REGIONAL_R0.get(tier, ZIKA_REGIONAL_R0["default"])

    if disease_name == "chikungunya":
        tier = CHIKUNGUNYA_COUNTRY_MAP.get(cc, "default")
        return CHIKUNGUNYA_REGIONAL_R0.get(tier, CHIKUNGUNYA_REGIONAL_R0["default"])

    if disease_name == "west nile":
        tier = WEST_NILE_COUNTRY_MAP.get(cc, "default")
        return WEST_NILE_REGIONAL_R0.get(tier, WEST_NILE_REGIONAL_R0["default"])

    return profile["r0"]

# -----------------------------
# COMPOSITE SCORING MODEL
# -----------------------------

def composite_score(disease_name, country_code=""):
    """
    Compute a 0-100 severity score for a disease using:
    - CFR (case fatality rate) — weighted 45%
    - R0 (reproduction number) — weighted 30%
    - Transmission route multiplier — weighted 15%
    - Vaccine availability penalty — weighted 10%

    For malaria and other vector-borne diseases, R0 is adjusted by country
    using regional incidence data (see COUNTRY_REGION_MAP and disease maps).

    For influenza, CFR and R0 are dynamically updated from CDC FluView via
    the Delphi Epidata API to reflect the currently dominant strain.
    Cached 24 hours. Falls back to seasonal averages if feed is unavailable.

    Sources:
    - CFR/R0 values: WHO fact sheets + JOGH 1996-2023 outbreak analysis
      https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12082254/
    - Transmission route severity: PMC airborne vs contact study
      https://pmc.ncbi.nlm.nih.gov/articles/PMC8013452/
    - Malaria regional R0: WHO Global Health Observatory via World Bank (2026)
      https://ourworldindata.org/grapher/incidence-of-malaria
    - Influenza strain: Delphi Epidata API / CDC FluView
      https://api.delphi.cmu.edu/epidata/fluview/
    """
    profile = DISEASE_PROFILES.get(disease_name)
    if not profile:
        return 10  # unknown disease — default low

    # For influenza: override CFR and R0 with live FluView strain data
    if disease_name == "influenza":
        strain_key     = get_fluview_strain()
        strain_profile = FLUVIEW_STRAIN_PROFILES.get(strain_key, FLUVIEW_STRAIN_PROFILES["fallback"])
        cfr_to_use = strain_profile["cfr"]
        r0_to_use  = strain_profile["r0"]
    else:
        cfr_to_use = profile["cfr"]
        r0_to_use  = get_r0_for_region(disease_name, country_code)

    cfr_norm          = normalize(cfr_to_use, CFR_MIN, CFR_MAX)
    r0_norm           = normalize(r0_to_use, R0_MIN, R0_MAX)
    transmission_mult = normalize(
        TRANSMISSION_MULTIPLIER.get(profile["transmission"], 1.0),
        TRANSMISSION_MIN, TRANSMISSION_MAX
    )
    vaccine_penalty   = 0.75 if profile["vaccine"] else 1.0

    raw = (
        WEIGHTS["cfr"]          * cfr_norm +
        WEIGHTS["r0"]           * r0_norm +
        WEIGHTS["transmission"] * transmission_mult +
        WEIGHTS["vaccine"]      * vaccine_penalty
    )

    return round(min(raw * 100, 100), 1)

def score_to_tier(score):
    """Map composite score to action tier."""
    if score >= TIER_THRESHOLDS["emergency"]:
        return "emergency"
    elif score >= TIER_THRESHOLDS["seek_care"]:
        return "seek_care"
    else:
        return "monitor"

def tier_recommendation(tier, diseases):
    """Return action recommendation based on tier and detected diseases."""
    disease_str = ", ".join(diseases) if diseases else "unknown disease"

    recommendations = {
        "emergency": (
            f"EMERGENCY: {disease_str} detected. Contact emergency services or your nearest "
            f"hospital immediately. Follow WHO guidance at https://www.who.int/emergencies/diseases"
        ),
        "seek_care": (
            f"SEEK CARE: {disease_str} activity detected in your area. Visit a clinic promptly. "
            f"Inform your provider of recent travel and symptoms."
        ),
        "monitor": (
            f"MONITOR: {disease_str} is present in WHO/CDC alerts. Practice standard precautions "
            f"and monitor symptoms. No immediate action required."
        ),
    }
    return recommendations.get(tier, "No recommendation available.")

# -----------------------------
# PREVENTION TIPS
# -----------------------------

def prevention_tips(country_code):
    cc = (country_code or "").lower()
    tips = [
        "Wash hands often (soap + water 20 seconds) or use sanitizer.",
        "Stay up-to-date on routine vaccines (including flu/COVID where appropriate).",
        "Avoid close contact with sick people; consider masking in crowded indoor spaces during surges.",
    ]

    if cc in {"us", "ca"}:
        tips += [
            "Watch respiratory virus trends (flu/RSV/COVID): ventilate indoor spaces.",
            "Use repellent and remove standing water during mosquito season.",
        ]
    elif cc in {"br", "mx", "co", "pe", "ar"}:
        tips += [
            "Mosquito protection (dengue/chikungunya): repellent, long sleeves, remove standing water.",
            "Use safe water practices if sanitation is uncertain (bottled/treated water).",
        ]
    else:
        tips += [
            "Use safe water practices when traveling (boil/filter if unsure).",
            "Use insect protection where mosquitoes/ticks are common.",
        ]

    return tips

def translate_text(text, target_lang):
    # MVP: no translation provider wired yet
    return text

# -----------------------------
# ROUTES: PAGES
# -----------------------------

@app.route("/")
def index():
    return render_template("index.html")

# -----------------------------
# ROUTES: API
# -----------------------------

@app.route("/api/location", methods=["POST"])
def api_location():
    payload = request.get_json(force=True)
    lat = float(payload["lat"])
    lon = float(payload["lon"])
    loc = reverse_geocode(lat, lon)
    return jsonify(loc)

@app.route("/api/news", methods=["GET"])
def api_news():
    who_items = get_cached_rss("who", WHO_RSS_URL)
    cdc_items = get_cached_rss("cdc", CDC_RSS_URL)
    items = who_items + cdc_items
    items.sort(key=lambda x: x.get("published") or "", reverse=True)
    return jsonify(items[:12])

@app.route("/api/threats", methods=["POST"])
def api_threats():
    payload = request.get_json(force=True)
    loc  = payload.get("location")
    dest = payload.get("destination")
    lang = payload.get("lang", "en")

    # Use destination for scoring if in travel mode, else use physical location
    scoring_loc = dest if dest else loc

    who_items = get_cached_rss("who", WHO_RSS_URL)
    cdc_items = get_cached_rss("cdc", CDC_RSS_URL)
    all_items = who_items + cdc_items

    # ── Hardcoded current outbreak alerts ──
    # These supplement RSS when feeds are unavailable or slow.
    # Updated to reflect confirmed active outbreaks as of June 2026.
    # Sources: WHO DON, CDC HAN, ECDC Threat Assessment
    HARDCODED_ALERTS = [
        {
            "title": "Ebola disease (Bundibugyo virus) outbreak — Democratic Republic of the Congo and Uganda",
            "summary": (
                "A major Ebola outbreak caused by Bundibugyo virus is ongoing in the Democratic Republic "
                "of the Congo (DRC) and Uganda. As of June 26 2026, over 1,155 confirmed cases and 304 "
                "deaths have been reported. The outbreak is centred in Ituri Province (DRC) with spread "
                "to North Kivu, South Kivu, Kinshasa, and Kampala (Uganda). WHO declared a Public Health "
                "Emergency of International Concern (PHEIC) on 17 May 2026. There is no approved vaccine "
                "or specific treatment for Bundibugyo virus. Travel to DRC and Uganda is at Level 3 risk "
                "(CDC). Avoid contact with sick individuals, healthcare facilities in outbreak zones, and "
                "funeral practices involving the deceased."
            ),
            "link": "https://www.who.int/emergencies/situations/ebola-outbreak---drc-2026",
            "published": "2026-06-26T00:00:00",
            "source": "who",
            "diseases": ["ebola"],
            "_hardcoded": True,
        },
        {
            "title": "Dengue fever — global record surge 2024–2026, Americas and Southeast Asia",
            "summary": (
                "Dengue cases reached record levels globally in 2024–2025. Brazil reported over 3.6 million "
                "cases in 2024. Indonesia, India, Philippines, Bangladesh, Colombia, Mexico, and Argentina "
                "are also severely affected. WHO declared a Grade 3 emergency. The Qdenga vaccine is being "
                "rolled out in select countries. Aedes aegypti mosquito control remains the primary prevention "
                "measure. Travelers to tropical regions should use DEET repellent and protective clothing."
            ),
            "link": "https://www.who.int/news-room/fact-sheets/detail/dengue-and-severe-dengue",
            "published": "2026-06-01T00:00:00",
            "source": "who",
            "diseases": ["dengue"],
            "_hardcoded": True,
        },
        {
            "title": "Mpox (Clade I) outbreak — Democratic Republic of the Congo and neighbouring countries",
            "summary": (
                "Mpox Clade I outbreak continues in the Democratic Republic of the Congo with cases reported "
                "in neighbouring countries including Uganda, Rwanda, Burundi, and Kenya. WHO declared a PHEIC "
                "in August 2024. The JYNNEOS vaccine is available for high-risk individuals. Avoid close "
                "physical contact with individuals with skin lesions or rash. DRC, Uganda, Rwanda, Burundi, "
                "Kenya travelers should take precautions."
            ),
            "link": "https://www.who.int/news-room/fact-sheets/detail/mpox",
            "published": "2026-05-01T00:00:00",
            "source": "who",
            "diseases": ["mpox"],
            "_hardcoded": True,
        },
        {
            "title": "Cholera — ongoing global alert, Yemen, Nigeria, DRC, Sudan, Haiti",
            "summary": (
                "Cholera outbreaks are ongoing in multiple countries including Yemen, Nigeria, Democratic "
                "Republic of the Congo, Sudan, Somalia, Haiti, and Ethiopia. WHO reports tens of thousands "
                "of cases annually in these regions. Waterborne transmission through contaminated water and "
                "food. Oral cholera vaccine is recommended for travel to endemic areas. Drink only bottled "
                "or boiled water."
            ),
            "link": "https://www.who.int/news-room/fact-sheets/detail/cholera",
            "published": "2026-06-01T00:00:00",
            "source": "who",
            "diseases": ["cholera"],
            "_hardcoded": True,
        },
        {
            "title": "Malaria — high endemic transmission, sub-Saharan Africa",
            "summary": (
                "Malaria transmission is ongoing throughout sub-Saharan Africa including Nigeria, DRC, "
                "Uganda, Tanzania, Kenya, Mozambique, Ghana, Cameroon, Mali, Burkina Faso, and Niger. "
                "WHO World Malaria Report 2025 estimates 263 million cases and 597,000 deaths annually. "
                "Antimalarial prophylaxis (Malarone, doxycycline) is strongly recommended for travelers. "
                "Use DEET repellent and insecticide-treated bed nets. The RTS,S vaccine is available in "
                "select African countries."
            ),
            "link": "https://www.who.int/news-room/fact-sheets/detail/malaria",
            "published": "2026-06-01T00:00:00",
            "source": "who",
            "diseases": ["malaria"],
            "_hardcoded": True,
        },
        {
            "title": "Yellow fever — endemic risk, sub-Saharan Africa and tropical South America",
            "summary": (
                "Yellow fever is endemic in sub-Saharan Africa and tropical South America including Brazil, "
                "Nigeria, DRC, Uganda, Cameroon, Ghana, Senegal, Burkina Faso, Mali, and Peru. WHO confirmed "
                "cases in 13 African countries in 2023. Vaccination is required for entry to many endemic "
                "countries and must be given at least 10 days before travel. The Carte Jaune (yellow card) "
                "serves as proof of vaccination at border crossings."
            ),
            "link": "https://www.who.int/news-room/fact-sheets/detail/yellow-fever",
            "published": "2026-06-01T00:00:00",
            "source": "who",
            "diseases": ["yellow fever"],
            "_hardcoded": True,
        },
        {
            "title": "Typhoid fever — high incidence, South Asia and sub-Saharan Africa",
            "summary": (
                "Typhoid fever remains highly endemic in Pakistan, India, Nepal, Bangladesh, Nigeria, DRC, "
                "Ethiopia, Ghana, and Cameroon. The Lancet SETA study (2024) confirmed burden exceeds 100 "
                "cases per 100,000 in these countries. Drug-resistant typhoid (XDR) is a growing concern "
                "in Pakistan. Typhoid conjugate vaccine (TCV) is recommended for travel to endemic regions. "
                "Consume only safe water and avoid raw foods."
            ),
            "link": "https://www.who.int/news-room/fact-sheets/detail/typhoid",
            "published": "2026-06-01T00:00:00",
            "source": "who",
            "diseases": ["typhoid"],
            "_hardcoded": True,
        },
        {
            "title": "West Nile virus — seasonal transmission, United States and Mediterranean",
            "summary": (
                "West Nile virus is endemic in the United States and Canada, with 2,000–5,000 human cases "
                "reported annually. Summer and early fall are peak transmission seasons. Italy, Greece, "
                "Romania, France, Hungary, and Spain report seasonal outbreaks annually. No approved human "
                "vaccine exists. Use insect repellent, wear long sleeves, and avoid outdoor activities at "
                "dawn and dusk in affected areas."
            ),
            "link": "https://www.who.int/news-room/fact-sheets/detail/west-nile-virus",
            "published": "2026-06-01T00:00:00",
            "source": "who",
            "diseases": ["west nile"],
            "_hardcoded": True,
        },
        {
            "title": "Chikungunya — Americas surge 2024–2026, South-East Asia endemic",
            "summary": (
                "Chikungunya incidence reached 43.9 per 100,000 in the Americas in 2024 (JOGH 2026). "
                "Brazil, Paraguay, Colombia, Bolivia, Mexico, and Central American countries are most "
                "affected. India reported 192,343 cases in 2024. Pakistan is the only country reporting "
                "active chikungunya in 2026 per ECDC. No widely deployed vaccine exists. Prevention "
                "relies on mosquito avoidance using DEET repellent and protective clothing."
            ),
            "link": "https://www.who.int/news-room/fact-sheets/detail/chikungunya",
            "published": "2026-06-01T00:00:00",
            "source": "who",
            "diseases": ["chikungunya"],
            "_hardcoded": True,
        },
        {
            "title": "Influenza H3N2 — severe 2025–2026 season, Northern Hemisphere",
            "summary": (
                "The 2025–2026 influenza season in the Northern Hemisphere was dominated by H3N2, which "
                "accounted for 91.2% of subtyped specimens at its peak (CDC FluView, January 2026). "
                "H3N2-dominant seasons trend more severe in elderly and immunocompromised populations. "
                "Annual influenza vaccination is strongly recommended before travel. Antiviral treatment "
                "with oseltamivir (Tamiflu) is most effective within 48 hours of symptom onset."
            ),
            "link": "https://www.cdc.gov/flu/weekly/index.htm",
            "published": "2026-01-15T00:00:00",
            "source": "cdc",
            "diseases": ["influenza"],
            "_hardcoded": True,
        },
    ]

    # Merge RSS items with hardcoded alerts — RSS first, hardcoded fill gaps
    rss_diseases = set()
    for item in all_items:
        diseases = extract_diseases_from_text(item)
        rss_diseases.update(diseases)

    # Only add hardcoded alert if RSS didn't already return something for that disease
    for alert in HARDCODED_ALERTS:
        if not any(d in rss_diseases for d in alert["diseases"]):
            all_items.append(alert)

    # ── Countries where disease burden is genuinely low ──
    LOW_RISK_COUNTRY_CODES = {
        "us", "ca", "gb", "fr", "de", "it", "es", "nl", "be", "se", "no", "dk",
        "fi", "ch", "at", "au", "nz", "jp", "kr", "sg", "il", "ie", "pt", "gr",
        "hu", "pl", "cz", "ro", "hr", "cy", "mt", "lu", "sk", "si", "ee", "lv", "lt",
    }

    # Diseases that have meaningful endemic presence in low-risk countries
    # Key: country_code → list of disease keys that are locally relevant
    LOW_RISK_RELEVANT_DISEASES = {
        "us": ["influenza", "rsv", "covid", "west nile"],
        "ca": ["influenza", "rsv", "covid", "west nile"],
        "gb": ["influenza", "rsv", "covid"],
        "fr": ["influenza", "rsv", "covid", "west nile"],
        "de": ["influenza", "rsv", "covid", "west nile"],
        "it": ["influenza", "rsv", "covid", "west nile"],
        "es": ["influenza", "rsv", "covid", "west nile"],
        "gr": ["influenza", "rsv", "covid", "west nile"],
        "au": ["influenza", "rsv", "covid", "dengue"],
        "jp": ["influenza", "rsv", "covid"],
        "kr": ["influenza", "rsv", "covid"],
        "sg": ["influenza", "covid", "dengue"],
    }
    DEFAULT_LOW_RISK_DISEASES = ["influenza", "rsv", "covid"]

    cc = (scoring_loc or loc or {}).get("country_code", "")
    is_low_risk = cc in LOW_RISK_COUNTRY_CODES
    country_code = cc

    enriched = []
    seen_diseases = set()

    if is_low_risk:
        # For low-risk countries: bypass alert text matching entirely.
        # Only score diseases that are genuinely endemic/seasonal there.
        # Never show tropical diseases (cholera, yellow fever, malaria, ebola etc.)
        relevant_diseases = LOW_RISK_RELEVANT_DISEASES.get(cc, DEFAULT_LOW_RISK_DISEASES)

        # Find the hardcoded alerts for these specific diseases only
        for alert in HARDCODED_ALERTS:
            disease_list = alert.get("diseases", [])
            matching = [d for d in disease_list if d in relevant_diseases]
            if not matching:
                continue
            for d in matching:
                if d in seen_diseases:
                    continue
                seen_diseases.add(d)
                score = composite_score(d, cc)
                tier  = score_to_tier(score)
                enriched.append({
                    **alert,
                    "diseases":       [d],
                    "risk_index":     score,
                    "tier":           tier,
                    "recommendation": tier_recommendation(tier, [d]),
                })

    else:
        # High-risk region: match alerts to location, fall back to all global alerts
        match_loc = scoring_loc or loc
        relevant = [it for it in all_items if alert_matches_user_location(it, match_loc)]
        if len(relevant) < 2:
            relevant = all_items

        for it in relevant:
            diseases = extract_diseases_from_text(it) or it.get("diseases", [])
            if not diseases:
                continue
            new_diseases = [d for d in diseases if d not in seen_diseases]
            if not new_diseases and not it.get("_hardcoded"):
                continue
            seen_diseases.update(new_diseases)
            primary_diseases = new_diseases if new_diseases else diseases
            score = max((composite_score(d, cc) for d in primary_diseases), default=10)
            tier  = score_to_tier(score)
            enriched.append({
                **it,
                "diseases":       primary_diseases,
                "risk_index":     score,
                "tier":           tier,
                "recommendation": tier_recommendation(tier, primary_diseases),
            })

    enriched.sort(key=lambda x: x["risk_index"], reverse=True)

    top = enriched[:5]
    avg = int(sum(x["risk_index"] for x in top) / len(top)) if top else 10

    band = "low"
    if avg >= TIER_THRESHOLDS["emergency"]:
        band = "high"
    elif avg >= TIER_THRESHOLDS["seek_care"]:
        band = "medium"

    tips = prevention_tips(country_code)
    top3 = tips[:3]

    tips_translated = [translate_text(t, lang) for t in tips]
    top3_translated = [translate_text(t, lang) for t in top3]

    flu_strain_key  = get_fluview_strain()
    flu_strain_info = FLUVIEW_STRAIN_PROFILES.get(flu_strain_key, FLUVIEW_STRAIN_PROFILES["fallback"])

    return jsonify({
        "risk_band":            band,
        "risk_index_avg_top":   avg,
        "top3_tips":            top3_translated,
        "tips":                 tips_translated,
        "alerts":               enriched[:10],
        "influenza_strain": {
            "label":  flu_strain_info["label"],
            "note":   flu_strain_info["note"],
            "cfr":    flu_strain_info["cfr"],
            "r0":     flu_strain_info["r0"],
            "source": "CDC FluView via Delphi Epidata API (api.delphi.cmu.edu)",
        },
        "note": (
            "Risk scores are derived from WHO/CDC epidemiological data "
            "(CFR, R0, transmission route, vaccine availability). "
            "Regional R0 adjusted per country. Influenza reflects current "
            "dominant strain per CDC FluView. Not a substitute for medical advice."
        ),
    })

@app.route("/api/nonprofits/nearby", methods=["POST"])
def api_nonprofits_nearby():
    """
    Returns health nonprofits, NGOs, clinics, and hospitals near a given location
    using the OpenStreetMap Overpass API — free, global, no API key required.

    Queries for nodes/ways tagged with:
      - office=ngo + (health|medical|humanitarian in name/tags)
      - amenity=clinic
      - amenity=community_centre + health-related tags
      - healthcare=* (any healthcare facility)

    Results are filtered to those within the requested radius and sorted by distance.
    Radius is accepted in miles but converted to meters for the Overpass query.

    Source: OpenStreetMap via Overpass API (overpass-api.de)
    License: ODbL — data is open and freely usable with attribution
    """
    payload = request.get_json(force=True)
    lat     = float(payload["lat"])
    lon     = float(payload["lon"])
    radius_miles  = float(payload.get("radius_miles", 25))
    radius_meters = int(radius_miles * 1609.34)  # convert miles → meters

    from math import radians, sin, cos, asin, sqrt

    def haversine_miles(lat1, lon1, lat2, lon2):
        R = 3958.8
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        return R * 2 * asin(sqrt(a))

    # Overpass QL query — searches for health-related organizations within radius
    # Uses 'around' filter centered on user's coordinates
    overpass_query = f"""
[out:json][timeout:25];
(
  node["office"="ngo"](around:{radius_meters},{lat},{lon});
  node["amenity"="clinic"](around:{radius_meters},{lat},{lon});
  node["amenity"="community_centre"]["healthcare"](around:{radius_meters},{lat},{lon});
  node["healthcare"](around:{radius_meters},{lat},{lon});
  node["amenity"="doctors"](around:{radius_meters},{lat},{lon});
  node["amenity"="hospital"](around:{radius_meters},{lat},{lon});
  way["amenity"="clinic"](around:{radius_meters},{lat},{lon});
  way["healthcare"](around:{radius_meters},{lat},{lon});
  way["amenity"="hospital"](around:{radius_meters},{lat},{lon});
);
out center tags;
""".strip()

    try:
        overpass_url = "https://overpass-api.de/api/interpreter"
        resp = requests.post(
            overpass_url,
            data={"data": overpass_query},
            headers={"User-Agent": USER_AGENT},
            timeout=30
        )
        resp.raise_for_status()
        osm_data = resp.json()

        results = []
        seen_names = set()  # deduplicate by name + approximate location

        for element in osm_data.get("elements", []):
            tags = element.get("tags", {})

            # Get coordinates — nodes have lat/lon directly, ways use center
            if element["type"] == "node":
                elat = element.get("lat")
                elon = element.get("lon")
            else:
                center = element.get("center", {})
                elat = center.get("lat")
                elon = center.get("lon")

            if elat is None or elon is None:
                continue

            # Get name — skip unnamed entries
            name = tags.get("name") or tags.get("name:en") or tags.get("operator")
            if not name:
                continue

            # Deduplicate
            dedup_key = f"{name[:20]}_{round(elat,3)}_{round(elon,3)}"
            if dedup_key in seen_names:
                continue
            seen_names.add(dedup_key)

            # Determine category for frontend filter chips
            amenity    = tags.get("amenity", "")
            healthcare = tags.get("healthcare", "")
            office     = tags.get("office", "")
            tag_str    = " ".join(tags.values()).lower()

            if any(w in tag_str for w in ["water", "sanitation", "wash", "hygiene"]):
                category = "water"
            elif any(w in tag_str for w in ["vaccin", "immuniz", "immunis"]):
                category = "vaccination"
            elif any(w in tag_str for w in ["travel", "tropical", "international"]):
                category = "travel health"
            elif amenity in ("hospital", "clinic", "doctors"):
                category = "travel health"
            elif healthcare:
                category = "travel health"
            elif office == "ngo":
                category = "general health"
            else:
                category = "general health"

            # Build result entry
            dist = haversine_miles(lat, lon, elat, elon)
            results.append({
                "name":           name,
                "type":           category,
                "lat":            elat,
                "lon":            elon,
                "distance_miles": round(dist, 2),
                "phone":          tags.get("phone") or tags.get("contact:phone") or "",
                "website":        tags.get("website") or tags.get("contact:website") or tags.get("url") or "",
                "address":        " ".join(filter(None, [
                    tags.get("addr:housenumber"),
                    tags.get("addr:street"),
                    tags.get("addr:city"),
                    tags.get("addr:country"),
                ])) or "",
                "opening_hours":  tags.get("opening_hours") or "",
            })

        # Sort by distance, cap at 25 results
        results.sort(key=lambda x: x["distance_miles"])
        return jsonify(results[:25])

    except Exception as e:
        print(f"[Overpass] query failed: {e}")
        return jsonify([]), 200  # return empty list gracefully — frontend handles it

@app.route("/api/pandemics", methods=["GET"])
def api_pandemics():
    pandemics = load_json_file(PANDEMICS_PATH, [])
    return jsonify(pandemics)

@app.route("/api/us-states-geojson", methods=["GET"])
def api_us_states_geojson():
    geo = load_json_file(US_STATES_GEOJSON_PATH, None)
    if geo is None:
        return jsonify({"error": "GeoJSON not found", "fix": "Add data/us_states_min.geojson"}), 404
    return jsonify(geo)

# -----------------------------
# ENTRYPOINT
# -----------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)