# Cities, Tiers & Resource Placement Plan

A research-backed pass over our current city roster (`tools/map_editor/dev_map_data/points.json`)
against the historical record of the **Economy of England in the Middle Ages**, focused on the
mid-medieval growth period (1100–1290). Goal: place resources realistically, re-tier cities to
reflect their real economic weight, and catalog notable towns we're missing.

Sources: Wikipedia — *Economy of England in the Middle Ages* (Mid-medieval growth 1100–1290;
Trade, manufacturing and the towns).

---

## 1. The historical resource geography

| Resource | Where it actually came from | Notes |
|---|---|---|
| **Wool** (the export engine) | Welsh Borders, **Lincolnshire**, the **Pennines**, Yorkshire | England's dominant export; Cistercian/Augustinian monastic flocks in the north. Shipped raw to Flanders. |
| **Cloth / textiles** | **Lincoln** (famous "Lincoln Scarlet"), **Stamford** | High-quality dyed cloth; most English wool left raw, so finished cloth towns were the exception. |
| **Tin** | **Cornwall** and **Devon** | Alluvial deposits; England's near-monopoly export in Europe. Stannary towns. |
| **Silver** | **Cumberland, Durham, Northumberland** | A semi-circle of mines producing 3–4 tonnes/yr — funded the coinage. |
| **Iron** | **Forest of Dean** (primary), Durham, the **Weald** | Forest of Dean is the marquee iron district. |
| **Lead** | **Yorkshire, Durham** and the north; also **Devon** | Roofing/plumbing for cathedrals and abbeys. |
| **Coal** | North-East (**Tyne**), 13th c. onward | "Sea-coal" shipped from the Tyne; bell-pits & strip mining. |
| **Salt** | **Lincolnshire** coast, **Droitwich** | Coastal evaporation + inland brine springs; produced for export. |
| **Fish / herring** | **Great Yarmouth**, **Scarborough** | The herring fishery; Yarmouth's autumn herring fair was nationally important. |
| **Wine** | Imported via **Bristol** (Gascony trade) & **Southampton**; produced in **Normandy** (our Caen/Rouen) | Bristol dominated the Gascon wine trade by the 13th c. |
| **Trade / finance hubs** | **London**, **York**, **Winchester**, **Lincoln**, **Norwich**, **Ipswich**, Thetford | Eastern towns linked to the sea by navigable rivers (York, Exeter, Lincoln acted as seaports). |

---

## 2. Our current cities → recommended resource + tier

Cities we already have (`points.json`). "Tier now → rec." flags changes worth making.

| City | Tier now → rec. | Resource(s) | Rationale |
|---|---|---|---|
| **London** | 5 → 5 | *trade/finance* (+ wine import) | Premier financial & luxury hub. No raw resource — it's a market node. |
| **Bristol** | 3 → **4** | **wine** | Dominated the Gascon wine trade by the 13th c. Undersized at tier 3. |
| **Lincoln** | 3 → **4** | **cloth** (+ nearby wool, salt) | Lincoln Scarlet cloth; seaport via the Witham; Lincolnshire wool & salt. Major mint. |
| **Newcastle** | 3 → **4** | **coal** | The Tyne coal trade — the defining northern export. Currently underweighted. |
| **York** | 1 → **4** | *trade* / wool | Second city of England, archbishopric, river seaport. Tier 1 is badly wrong. |
| **Norwich** | 3 → 3 | wool / *trade* | One of the largest English towns; eastern textile region. |
| **Great Yarmouth** | 1 → **3** | **fish (herring)** | Nationally significant herring fishery & fair. Bump from tier 1. |
| **Gloucester** | 3 → 3 | **iron** | Gateway to the **Forest of Dean** iron district. |
| **Exeter** | 1 → **2** | **tin** / lead | Seaport draining Devon's tin & lead country. |
| **Plymouth** | 1 → 2 | **tin** | Devon/Cornwall tin outlet (though Plymouth grows later). |
| **Winchester** | 3 → 3 | wool / *trade* | Old capital, great wool fair (St Giles). |
| **Hereford** | 2 → 2 | **wool** | Welsh-border wool country. |
| **Shrewsbury** | 2 → 2 | **wool** | Welsh-border wool & cloth staple. |
| **King's Lynn** | 3 → 3 | *trade* / salt | Major east-coast wool & grain export port; Lincolnshire salt nearby. |
| **Ipswich** | 1 → 2 | *trade* | Eastern port town listed among the key towns. |
| **Bury St. Edmunds** | 3 → 3 | wool | Abbey town in the eastern wool region. |
| **Leicester / Nottingham / Oxford** | — | wool / *trade* | Midlands market towns; Oxford also a learning centre. |
| **Beverley / Hull** | 3 / 1 | wool / *trade* | Yorkshire wool export ports (Hull grows fast — consider 1 → 2). |
| **Caen / Rouen / Callais (Calais)** | 3/2/3 | **wine** / *trade* | Normandy — cross-Channel trade & wine; keep as continental economic anchors. |

**Scotland (alba) / Ireland — realism-informed (outside the English article's scope):**

| City | Resource | Rationale |
|---|---|---|
| **Edinburgh** | *trade* / wool | Scottish wool export (to Flanders) & burgh trade. |
| **Aberdeen (Berdeen)** | **fish** | North Sea fishery & salmon. |
| **Caithness / Orkney / Shetland** | **fish** | Northern fisheries. |
| **Dublin / Waterford / Cork / Galway** | *trade* / **fish** | Hiberno-Norse trade ports; fishing & hides export. |

---

## 3. Missing cities worth adding (catalog for placement)

Notable economic towns the map doesn't have yet. Rough positions to trace later.

| Town | Region | Why it matters | Suggested resource / tier |
|---|---|---|---|
| **Southampton** | S coast (Hampshire) | Major wine & wool port, Italian galley trade | wine/trade, tier 3–4 |
| **Boston** | Lincolnshire (the Wash) | One of England's biggest wool-export ports in the 13th c.; huge fair | wool/trade, tier 4 |
| **Scarborough** | N Yorks coast | The other great herring port alongside Yarmouth | fish, tier 2–3 |
| **Stamford** | Lincolnshire | Famous cloth town (with Lincoln) | cloth, tier 2–3 |
| **Droitwich** | Worcestershire | Inland brine-spring salt centre, produced for export | salt, tier 2 |
| **Cornwall stannary town** (Bodmin / Lostwithiel / Truro) | Cornwall | The heart of tin mining — we have no Cornwall city at all | tin, tier 2 |
| **Coventry** | W Midlands | Rising cloth/textile town | cloth, tier 2–3 |
| **Durham** | NE | Silver/lead mining district + prince-bishopric | silver/lead, tier 3 |
| **Salisbury (New Sarum)** | Wiltshire | Wool & cloth market, new 13th-c. town | wool, tier 2 |
| **Southampton–Winchester** already covered above. | | | |

Cornwall is the biggest gap: it's the entire English tin supply and we currently place nothing there.

---

## 4. Resource layer: now landmark points, not a brush

**Done.** Resources are no longer a painted brush region — they're **landmark
points, exactly like cities**. Each deposit is one dot placed at its real
geography (`layers/resources.json` is now `input: point`,
`point_coupling: free`, with a `kind` field of the new `type: category`
that picks its color from the legend). This mirrors the cities layer and
means a resource is a *place on the map* you can see and route roads to,
not a diffuse painted blob.

Pipeline/engine notes:
- The map editor gained a generic `category` point-field type (mapfmt /
  export / editor.js) — nothing names "resources" specially.
- `growth.py`'s `ATTRACTION_KEYS` and `roads.py`'s `RESOURCE_TARGET_WEIGHT`
  now list the full resource set below, so growth bends toward and roads
  route to every resource landmark. `gold` was dropped (no medieval
  English gold); **silver** is the historical precious metal.
- Resources no longer `reduce` into a per-province tag. If a future economy
  needs "which province holds what", derive it from the landmark points the
  same way `city_position` is derived — don't reintroduce the brush.

Current `layers/resources.json` legend keys:

Original: **iron, timber, wine, salt** (gold removed). Extended with:

- **wool** — the single most important addition (Lincolnshire, Pennines, Welsh borders, Yorkshire)
- **cloth** — Lincoln, Stamford, Coventry
- **tin** — Cornwall/Devon
- **coal** — Tyne / Newcastle
- **lead** and **silver** — northern mining (could fold silver into a "silver/lead" key)
- **fish** — Yarmouth, Scarborough, Aberdeen, the isles

**Timber** is kept (Weald/forests) but wasn't a headline export.

Final palette (as shipped in `layers/resources.json`):
`iron #9aa0a6`, `timber #7b3f00`, `wine #8e2f4a`, `salt #e8e8e8`,
`wool #c9b79c`, `cloth #b5495b`, `tin #6b8fa3`, `coal #2b2b2b`,
`lead #4a5560`, `silver #cfd8dc`, `fish #4a7fa5`.

### Resource landmarks placed so far

An initial, deliberately-not-exhaustive set (in `dev_map_data/project.json`,
snapped to land from real lon/lat): Cornish & Dartmoor **tin**, Forest of
Dean **iron**, Tyne **coal**, Lincolnshire / Pennine / Welsh-border **wool**,
Lincoln **cloth**, Durham **silver**, Dales **lead**, Droitwich **salt**,
Yarmouth / Scarborough / Aberdeen **fish**, Norman **wine**, and Weald
**timber**. Add more by placing points in the editor (resources layer → click
the map → pick a kind), or by lon/lat as above.

---

## 5. Suggested next steps

1. ~~Confirm resource keys and update the legend~~ — **done** (§4).
2. ~~Model resources as landmarks, not a brush~~ — **done** (§4).
3. Re-tier the cities flagged in §2 (edit `project.json`'s cities points).
4. Add the missing towns in §3 (place city points), and place their resource
   landmarks the same way (resources layer → click → pick a kind).
5. Run `make map-editor-preview`, then `make promote-map`.

---

# Part II — Scotland, Wales, Ireland & Northern France

A second research pass extending the roster beyond England, from the Wikipedia
articles on *Scotland / Wales / Ireland in the High/Middle Ages* (and the
*Economy of Scotland in the Middle Ages* and *History of Ireland 1169–1536* for
the town/trade detail the summary articles lacked) plus *France in the Middle
Ages*. Cities below were **placed into `dev_map_data/project.json`** (source of
truth; `points.json` is derived) with pixel coords projected from real lon/lat
through the map georef. Resources are **catalogued here for later painting**,
not yet brushed onto `resources.png`.

## 6. Tiering scale used

`5` London-class metropolis · `4` major regional city / great trade port ·
`3` significant town · `2` notable town / minor burgh · `1` small settlement.
Tiers reflect *medieval* weight, so several modern-populous but medievally
minor towns (Glasgow, Inverness) were tiered **down**, and the historic wool /
trade ports (Berwick, Waterford) up.

## 7. Scotland (`alba`)

The wealthiest burghs were the **east-coast** ports; the southwest (Glasgow,
Ayr) was secondary, trading with Ireland. Berwick was the single richest burgh
(the great Scottish wool staple) before it was lost to England.

**Re-tiered existing:** Aberdeen (`p43`) 3→**4**; Perth (`ukc-94`) 1→**4**
(royal centre, top-4 burgh); Dunfermline (`ukc-79`) 2→**3**; Whithorn (`p45`)
1→**2** (Irish-Sea trade); Glasgow (`ukc-4`) 4→**2**, Inverness (`ukc-95`)
1→**2**, Ayr (`ukc-98`) 1→**2**, Dumfries (`ukc-117`) 1→**2**. Edinburgh
(`p4`) kept at 4.

**Added:** Berwick **t4**, Roxburgh t3, Dundee t3, Stirling t3, St Andrews t3,
Elgin t2, Kirkcudbright t1.

| Resource | Where (paint later) |
|---|---|
| **wool** / **hides** | Border burghs & Lammermuir/Tweeddale hinterland (Berwick, Roxburgh) — the major exports |
| **fish** (cod, herring, **salmon**) | Aberdeen, Berwick, the north-east & Moray coast (Elgin), Firth of Forth |
| **salt** | Firth of Forth coastal pans (near Edinburgh/Dunfermline) |
| **coal** | Forth / Lothian ("sea-coal", 13th c. onward) |
| *trade* | Edinburgh, Perth, Aberdeen, Dundee, Stirling |

## 8. Wales

Norman/Marcher **boroughs** and the later Edwardian **castle-towns** are the
urban layer; native Wales was pastoral (cattle + upland wool). Carmarthen and
Pembroke were the principal southern towns; Cardiff/Swansea the southern
seaports; Caernarfon/Conwy the Edwardian northern centres.

**Re-tiered existing:** Cardiff (`p6`) 4→**3** (realistic Norman borough).
Swansea/Abertawe (`ukc-19`) t3, Newport (`ukc-29`) t3 kept.
*Note:* `ukc-11` "Caerdydd" is a **near-duplicate of Cardiff** (`p6`); worth
deleting one on a cleanup pass. Same for the two **Galway** points (`p31`,
`p48`).

**Added:** Caernarfon t3, Carmarthen t3, Pembroke t3, Conwy t2, Cardigan t2,
Haverfordwest t2, Brecon t2, Aberystwyth t2, Beaumaris t2, Denbigh t2,
Harlech t1, Abergwyngregyn t1 (Llywelyn's north-coast capital).

| Resource | Where (paint later) |
|---|---|
| **cloth** | **Pembrokeshire** (Flemish cloth industry under Henry I — Pembroke/Haverfordwest) |
| **wool** | Welsh uplands & Marches (Brecon, mid-Wales) |
| *cattle* (no legend key yet) | Pura Wallia uplands — drovers' economy; could fold into "wool" or add a key |
| **lead**/**silver** | Cardiganshire hills (Aberystwyth hinterland — later medieval mining) |

## 9. Ireland

Hiberno-Norse + Cambro-Norman **walled port towns** dominate; Dublin was the
primary city and Waterford the second royal city (both proclaimed royal by
Henry II, 1171). Trade centred on hides/wool export and wine import.

**Re-tiered existing:** Dublin (`p3`) 5→**4** (biggest Irish city but below
London); Wexford (`p49`) 4→**3**; Limerick/Liberick (`p50`) 1→**3**
(major Hiberno-Norse city); Cork (`p47`) kept t3.

**Added:** Waterford **t4**, Drogheda t3, Kilkenny t3, New Ross t3, Youghal t2
(snapped onto the estuary coast), Trim t2, Carrickfergus t2, Dundalk t2,
Wicklow t1.

| Resource | Where (paint later) |
|---|---|
| **fish** | Southern & eastern ports (Waterford, Cork, Youghal, Wexford) — herring & sea fisheries |
| **hides** (no key; use "wool"/*trade* or add) | Norman manorial hinterlands — the marquee Irish export |
| *trade* / **wine** import | Dublin, Waterford, New Ross, Cork (Gascon wine) |

## 10. Northern France

**Only the Channel fringe fits the map bbox** (`lat ≥ 49`, `lon ≤ 3`). Paris
(48.85 °N) and Reims (lon 4 °E) fall **outside** and cannot be placed —
France is deliberately peripheral here. Rouen was France's second-largest city.

**Re-tiered existing:** Rouen/Rouan (`p14`) 2→**4**; Caen (`p13`) kept t3;
Calais/Callias (`p12`) kept t3.

**Added:** Boulogne t3, Amiens t3 (cloth), Dieppe t2, Bayeux t2, Cherbourg t2,
Harfleur t2 (the medieval port by modern Le Havre).

| Resource | Where (paint later) |
|---|---|
| **wine** | Normandy (Caen/Rouen) — the cross-Channel wine anchor already noted in §1 |
| **cloth** | Picardy / Amiens (and the Flanders-facing north) |
| **salt** | Norman & Picard coastal pans |
| **fish** | Boulogne, Dieppe (Channel fisheries) |

## 11. Legend gaps to decide

The current `resources.json` legend has no **hides** or **cattle** key. Both are
central to the Scottish/Irish/Welsh pastoral economies. Options: fold hides into
*wool* and cattle into *wool*/uplands, or add `hides`/`cattle` legend keys.
Everything above is placed at the **city** level; the resource **landmarks**
for §7–§10 are still to be placed (as points now — see §4, not a brush).
