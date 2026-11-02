# Sources behind the digital twin

Published figures only. `python -m publicbank.places --build` turns these into `../goa_divisions.json`;
`python -m publicbank.schemes --import <xlsx>` turns the schemes workbook (kept outside the repo) into
`../goa_schemes.json`.

## goa_local_bodies.csv — Local Government Directory, every local body in Goa (205 rows)

Ministry of Panchayati Raj, Local Government Directory (https://lgdirectory.gov.in), read from the daily parquet
mirror at https://ramseraph.github.io/opendata/lgd/ (snapshot of 2026-11-01): 191 village panchayats, 13 municipal
councils and the City Corporation of Panaji, the totals the State Election Commission and the Directorate of
Panchayats give. Fields: kind, name (LGD's spelling), taluka, district (current LGD district: North Goa, South Goa,
and Kushavati, notified 31 Dec 2025 for Quepem, Sanguem, Canacona and Dharbandora; Ponda has been in South Goa since
January 2015), district_census_2011 (the 2011 parent district), LGD local-body code, the census town code for a
town, ward count (distinct ward codes in LGD's ward tables; Bardez's 279 matches the SEC figure), aliases (spellings
seen in the Goa Legislative Assembly's LAQ 188 annexure, the census and SEC snippets, separated by |), and the
Census 2011 codes of the body's villages per LGD. The Directorate of Panchayats and the State Election Commission
sites could not be read by machine (login wall, HTTP 403), so LGD is the official list used here.

## goa_pca_villages.csv — Census 2011, Primary Census Abstract, village and town level, Goa (405 rows)

Office of the Registrar General & Census Commissioner, Census 2011 data catalogue (NADA), "PCA TV: Primary census
abstract at town, village and ward level, Goa":

- North Goa (district 585): https://censusindia.gov.in/nada/index.php/catalog/6776 (`DDW_PCA3001_2011_MDDS with UI.xlsx`)
- South Goa (district 586): https://censusindia.gov.in/nada/index.php/catalog/6777 (`DDW_PCA3002_2011_MDDS with UI.xlsx`)

Village (Rural) and Town (Urban) rows only; district, sub-district and ward rows dropped. Panaji appears twice under
one code, as "Panaji (M Corp. + OG)" (70,991, the town with its outgrowths) and "Panaji (M Corp.)" (40,017, the
corporation alone); the build uses the corporation alone, since the outgrowths are the villages of the five
neighbouring Tiswadi panchayats (Taleigao, St. Cruz, Merces, Chimbel, Se-Old-Goa), which the census did not
publish separately. Those five, and Harvalem (a panchayat made after 2011), therefore carry no census figures.
Without the outgrowth row the population column sums to 1,458,545, the published state total. `cultivators_total`
etc. are main + marginal workers, the usual headline figure. Codes are MDDS codes.

## goa_village_to_panchayat.csv — Local Government Directory, census village → local body (410 rows)

Ministry of Panchayati Raj, Local Government Directory (https://lgdirectory.gov.in), via the India Data Portal's
mirror "LGD codes — Villages LGD Codes", snapshot of 2023-10-20:
https://ckandev.indiadataportal.com/dataset/lgd-codes/resource/cb81a7a4-a3bc-4962-a1eb-838a1db5bfdb

For Goa the LGD village code is the Census 2011 MDDS code, so the join is by code. All 334 census villages and the
56 census towns match; 333 villages fall under 189 of the 191 village panchayats, the village Pernem is inside
Pernem Municipality, and six units sit in two bodies and carry two rows (the build splits their figures half and
half). The 14 statutory towns are urban local bodies, not panchayat villages. The mapping reflects 2023 boundaries,
not 2011. The build joins this file to goa_local_bodies.csv by LGD local-body code.

## goa_marine_fishing_villages_cmfri_2016.csv — CMFRI Marine Fisheries Census 2016, Goa (41 villages)

Central Marine Fisheries Research Institute, Marine Fisheries Census 2016, Goa report:
https://eprints.cmfri.org.in/17501/ . One row per marine fishing village (taluka, fisher families, traditional
families, BPL families, fisherfolk population, active fisherfolk, cooperative membership, craft owned); village
totals match the report's totals. Villages, not panchayats: the build matches each village to the body it lies in
(see goa_name_fixes.csv) and sets that body's coastal flag and fisher-household count. Thirteen hamlets found no body
and are listed in the build output.

## goa_dairy_cooperative_societies_2008.csv — dairy cooperative societies by place (167 rows)

Office of the Registrar of Cooperative Societies, "List of the Dairy Societies Taluka-wise as on 31/5/2008":
https://www.goa.gov.in/wp-content/uploads/2020/09/List-of-the-Dairy-Societies-Taluka-Wise.pdf . Old, but the only
public village-named list; the Goa Dairy union today reports a similar count. The build counts societies per body
where the place name matches a body's village (about a hundred); hamlet names that match nothing are left out.

## goa_st_population_by_panchayat.csv — Scheduled Tribe population per body (204 rows)

Directorate of Tribal Welfare, Government of Goa, "Statistical data" (Census 2011 basis, panchayat-wise):
https://tribalwelfare.goa.gov.in/images/stastic-data.pdf . The PDF's text layer is scrambled; the table was OCR'd
page by page and the per-taluka sums reconciled with the sheet's own totals. The build uses it in two ways: to fill
population and ST count for the bodies the census file cannot (the five Tiswadi panchayats inside Panaji's
outgrowth), and to cross-check the census-derived ST counts elsewhere (the disagreements are printed at build time
and are mostly boundary and spelling differences between the two sources).

## goa_villages_2011.geojson — village boundary polygons (402 features)

DataMeet community, *Indian Village Boundaries*, Goa folder: https://github.com/datameet/indian_village_boundaries/tree/master/ga
(derived by DataMeet from Census of India village maps), Open Database License 1.0 (attribution and share-alike for
derived databases: this build's `goa_map.json` is such a derivative and carries the attribution). 358 villages and
44 census towns as polygons in WGS84, with DataMeet's 2001 location code and, joined from DataMeet's own `ga.csv`,
the Census 2011 MDDS village code on 389 of them; the 13 statutory-town polygons carry names only and are matched
to the municipal bodies by name. `python -m publicbank.places --build-map` dissolves the villages into local bodies
through goa_village_to_panchayat.csv (a village in two bodies is drawn once, in the body that would otherwise have
no shape), then bodies into talukas and districts, simplifies the outlines (about 40 m) and projects them to plain
map units; needs shapely at build time only. Three bodies have no shape: Harvalem (no 2011 village), and Taleigao
and Merces, whose villages the source drew inside Panaji's polygon.

## The business portals (merged into goa_schemes.json by `schemes.py --import … --business … --services …`)

- **goa_msme_schemes.csv** (74 rows, seen 2 Nov 2026): https://msme.goa.gov.in/ , the Directorate of Industries,
  Trade and Commerce's portal. Every page under Schemes › State Government Schemes (49: the Umbrella Scheme 2017,
  the Goa IT Policy 2018 schemes, the Goa Start-up Policy 2025, the Logistics and Warehousing Incentives 2025, the
  tribal finance corporation's loans, two tourism schemes, ODOP cashew), Schemes › Central Ministry Schemes (15),
  About › RAMP (7) and the DITC services (3). Eligibility, benefit, documents and steps are verbatim from the
  pages; dates come from the gazette PDFs linked there (24 of 117 PDFs are scanned images and could not be read; the
  rows say so). The portal's own slips are noted per row: benefits copied between two pages, a Rs 5 crore / Rs 10
  crore inconsistency for CGTMSE, Umbrella Scheme sub-schemes whose notification says they expired on 30 Sep 2022
  but which the portal still shows as open.
- **goa_startup_schemes.csv** (61 rows): https://startup.goa.gov.in/ , the Startup and IT Promotion Cell. The live
  site's TLS certificate has expired, so every page was read from the Internet Archive's latest capture
  (2020–2026; the capture date is in each row's notes and the original URL in source_url). Nine scheme pages of
  the Startup Policy 2021, five 2017 scheme PDFs, policy-level incentives of the 2017, 2021 and 2025 policies, the
  cell's services and programmes, and the IT Policy 2018 schemes linked under Notifications.
- **goa_online_services.csv** (212 rows): https://goaonline.gov.in/AllServices , read from the portal's own
  AllServices.json and each service's public detail page. 282 distinct services across 49 departments on the
  portal; the 212 kept are the ones a shop, workshop, hotel or shack, farm, dairy, fishing unit, transport, IT or
  start-up unit, cooperative, NGO or SHG would use (registrations, licences, approvals, scheme applications,
  renewals, returns, dues, and the certificates scheme forms ask for). 58 are the application channel for a named
  scheme. No fees are stated on the portal; 47 rows carry a processing time. Not on the portal: Udyam and FSSAI
  (central), Social Welfare, Tribal Welfare, Handicrafts, house tax; the panchayat trade licence is live in seven
  panchayats only.

How they merge: a portal row whose name (or core name, with years and "(Umbrella Scheme)"-style tails stripped)
equals a workbook scheme's gains the portal as a source, the page link and any blank fields; otherwise it becomes
a scheme of its own with `sources` naming the portal and `tags` (startup, msme, artisan, incubator) from the
portal's own "who can apply". A Goa Online service whose linked scheme name resolves puts `apply_online` on that
scheme, shown on the twin page as "apply on Goa Online".

## goa_name_fixes.csv — hand-kept name resolutions

A source's place name → the body it lies in, with the basis in words, for names the automatic matching (same word in
the body's name, alias or village list within the taluka; close spelling within the taluka; a unique same-named body
elsewhere for whole-body names) cannot resolve. Local geography, unverified by any department; each line says why.

## Coastline and the figures still missing

A body with a CMFRI marine fishing village is coastal; every body in Bicholim, Sattari, Ponda, Dharbandora and
Sanguem (talukas with no coastline) is not; the rest stay unknown and show "?" on the page. Tidal-water (estuarine)
flags are not set yet. Per-panchayat MGNREGA job cards, self-help groups, PMAY-G houses and the weaker-panchayat list
could not be fetched: the MGNREGA and NRLM portals answer 401 and 403 to every request, and the Directorate of
Panchayats site is behind a login. `goa_programmes.csv` (id or lgd_code, key, value, as_of, source) and
`goa_place_flags.csv` (id or lgd_code, coastal, estuarine, source) are read by the build when present.
