"""
The validation corpus: every real error case from the three error catalogs
(MVP1/validation/docs) translated into the metadata module's input shapes.

Each fixture is:
  {
    "id": str,
    "source": str,            # which catalog + section
    "summary": str,
    "kind": "caught" | "gap" | "synthetic",
      - caught:    the module's checks DO flag the catalog's error
      - gap:       the module misses the catalog's error (documented gap)
      - synthetic: not from a catalog; proves a check works on a clean case
    "expected_checks": [int],        # check_ids that MUST fire
    "unexpected_ok": [int] = [],     # check_ids allowed to fire beyond expected
    "notes": str = "",               # gap explanation / reconstruction caveats
    "sentences": [{"id", "text"}],   # body sentences with [N] citation markers
    "body_text": str,                # raw body text (check 3 scans this)
    "references": [{"number", "raw"}],
    "actual_records": {ref_id: record_dict_or_case_id},
    "journal_range_cache": {issn: [(year, vol), ...]},  # injected for check 11
  }

`actual_records` values reference validation/real_records.json by case_id (the
harness resolves them) or are inline dicts. This keeps the corpus readable
while the records stay verified ground truth.

The corpus draws on:
  - Doc_Catalogo_Errores_ChatGPT (1).txt   -> Doc1/Doc2 (ChatGPT) A1-A5
  - Doc_Catalogo_ErroresGemini (2).txt     -> Doc3 (Gemini) B1-B4
  - doc8_deteccion_errores_consensus.txt   -> Doc8 (Consensus)
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_REAL = json.loads(
    (ROOT / "validation" / "real_records.json").read_text(encoding="utf-8")
)


def _rec(case_id: str):
    return _REAL[case_id]


# --- shared declared raw strings (module's APA target format; the catalog's
# error VALUES are preserved verbatim, punctuation normalized so the parser
# can extract them) ---

N_A_Y = "Naya-Català, F., Simó-Mirabet, P., Calduch-Giner, J., & Pérez-Sánchez, J. (2021). Transcriptomic profiling of Gh/Igf system reveals a prompted tissue-specific differentiation and novel hypoxia responsive genes in gilthead sea bream. Scientific Reports, 11(1), 16466. https://doi.org/10.1038/s41598-021-95408-6"

FIXTURES = [
    # ======================================================================
    # CHECK 1 -- duplicate DOI under two reference numbers (CAUGHT)
    # ======================================================================
    {
        "id": "doc1_a2_4_duplicate_doi",
        "source": "Doc1 (ChatGPT) A2.4 + A2.6",
        "summary": "Same paper (10.3390/ijms252413583) listed twice under different numbers with two contradictory fabricated author/year attributions (Duarte-Paternina 2024 vs Zhao 2022).",
        "kind": "caught",
        "expected_checks": [1, 7, 16],
        "notes": "check_01 fires on both numbers (shared DOI). The fabricated first authors (Duarte-Paternina, Zhao) are also caught by check_07: the declared title matches the real record well above 0.85, and neither fabricated surname is in the real author list (Dong, Hou, Li, Xu, Gao). check_16 catches the fabricated year on ref 84 (2022 vs the record's 2024); ref 57's declared 2024 matches.",
        "sentences": [
            {"id": "s1", "text": "The GH-IGF axis response to photoperiod is described in refs [57][84]."},
        ],
        "body_text": "The GH-IGF axis response to photoperiod is described in refs [57][84].",
        "references": [
            {"number": 57, "raw": "Duarte-Paternina, F., et al. (2024). Extended Photoperiod Facilitated the Restoration of the Expression of GH-IGF Axis Genes in Submerged Rainbow Trout (Oncorhynchus mykiss). International Journal of Molecular Sciences, 25(24), 13583. https://doi.org/10.3390/ijms252413583"},
            {"number": 84, "raw": "Zhao, K., et al. (2022). Extended Photoperiod Facilitated the Restoration of the Expression of GH-IGF Axis Genes in Submerged Rainbow Trout (Oncorhynchus mykiss). International Journal of Molecular Sciences, 25(24), 13583. https://doi.org/10.3390/ijms252413583"},
        ],
        "actual_records": {57: _rec("doc1_a2_4_duplicate_doi"), 84: _rec("doc1_a2_4_duplicate_doi")},
    },
    {
        "id": "doc3_b4_9_15_shared_url",
        "source": "Doc3 (Gemini) B4 #9/#15",
        "summary": "Two distinct declared references (Guan & Qiu 2020; Liu Z et al. 2020) carrying the same URL/DOI 10.1080/09712119.2024.2440045, which is really a single 2024 paper (Ding et al.).",
        "kind": "caught",
        "expected_checks": [1, 7, 14, 16],
        "unexpected_ok": [9],
        "notes": "check_01 fires on the shared DOI; both fabricated first authors (Guan, Liu) fail check_07; both declared years (2020) fail check_16 against the record's real 2024. check_14 also fires on ref 9: it declares only 2 authors with no truncation marker while the real record has 4 (Ding, Cao, Cao, Bing) -- a silent omission the module catches. check_09 is tolerated: the catalog gives no declared titles, and the reconstructed title lands in the 0.85-0.97 band because the real record's title embeds <i> markup that normalization cannot fully bridge (similarity 0.90) -- an artifact of the reconstruction, not the catalog's error.",
        "sentences": [
            {"id": "s1", "text": "The liver and brain transcriptome of mandarin fish was characterized in [9] and [15]."},
        ],
        "body_text": "The liver and brain transcriptome of mandarin fish was characterized in [9] and [15].",
        "references": [
            {"number": 9, "raw": "Guan, X., & Qiu, Y. (2020). Characterization of the growth-related transcriptome in the Liver and Brain of mandarin fish (Siniperca chuatsi) through RNA-Seq analysis. Journal of Applied Animal Research, 52(1), 2440045. https://doi.org/10.1080/09712119.2024.2440045"},
            {"number": 15, "raw": "Liu, Z., et al. (2020). Characterization of the growth-related transcriptome in the Liver and Brain of mandarin fish (Siniperca chuatsi) through RNA-Seq analysis. Journal of Applied Animal Research, 52(1), 2440045. https://doi.org/10.1080/09712119.2024.2440045"},
        ],
        "actual_records": {9: _rec("doc3_b4_9_15_guan_liu"), 15: _rec("doc3_b4_9_15_guan_liu")},
    },

    # ======================================================================
    # CHECK 3 -- declared count vs listed (GAP + synthetic positive)
    # ======================================================================
    {
        "id": "doc8_declared_count_gap",
        "source": "Doc8 (Consensus)",
        "summary": "Document claims 'los 50 artículos más relevantes' / '50 Included' (flowchart) but lists 37 references.",
        "kind": "gap",
        "expected_checks": [],
        "unexpected_ok": [10],
        "notes": "check_03 abstains: its count-phrase regex is English-only (studies|references|articles|papers|trials|reports) and neither 'los 50 más relevantes' nor '50 Included' matches. The 50-vs-37 discrepancy is therefore missed. Fixture uses 37 minimal references (content irrelevant; check_03 only counts); the citing sentence [1-37] exists only so check_13 does not fire on never-cited entries.",
        "sentences": [{"id": "s1", "text": "Los 37 estudios fueron analizados en la revisión [1-37]."}],
        "body_text": "Tras el cribado se seleccionaron los 50 artículos más relevantes (50 Included) para el análisis de la vía GH/IGF. Los 37 estudios fueron analizados en la revisión [1-37].",
        "references": [{"number": i, "raw": f"Dummy{i}, A. (20{i%90 + 1936 if i % 90 <= 87 else 2020}). Placeholder title. Journal, {i}, {i}. https://doi.org/10.5555/dummy.{i}"} for i in range(1, 38)],
        "actual_records": {},
    },
    {
        "id": "synthetic_check_03_english_count",
        "source": "synthetic (capability)",
        "summary": "English count phrase that check_03's regex CAN parse, mismatching the bibliography length.",
        "kind": "synthetic",
        "expected_checks": [3],
        "notes": "Not from a catalog. Proves check_03 fires when the phrase is in its supported English set -- isolating the Doc8 miss to language coverage, not logic. The citing sentence avoids the word 'studies' so check_03 sees exactly one count phrase.",
        "sentences": [{"id": "s1", "text": "These references were analyzed in our review [1-30]."}],
        "body_text": "We included 37 studies in this systematic review of the GH/IGF axis. These references were analyzed in our review [1-30].",
        "references": [{"number": i, "raw": f"Dummy{i}, A. (2020). Placeholder title. Journal, {i}, {i}. https://doi.org/10.5555/dummy.{i}"} for i in range(1, 31)],
        "actual_records": {},
    },

    # ======================================================================
    # CHECK 7 -- fabricated author over a real DOI
    # ======================================================================
    {
        "id": "doc3_b1_3_berishvili_clean",
        "source": "Doc3 (Gemini) B1 #3 (clean)",
        "summary": "Berishvili 2010, one of the 3 clean APA entries (all metadata correct). Negative control.",
        "kind": "synthetic",
        "expected_checks": [],
        "notes": "Negative control: a fully correct entry must produce zero findings across every check.",
        "sentences": [{"id": "s1", "text": "IGF-3 expression in tilapia gonads was reported by [3]."}],
        "body_text": "IGF-3 expression in tilapia gonads was reported by [3].",
        "references": [
            {"number": 3, "raw": "Berishvili, G., Baroiller, J.-F., Eppler, E., & Reinecke, M. (2010). Insulin-like growth factor-3 (IGF-3) in male and female gonads of the tilapia: Development and regulation of gene expression by growth hormone (GH) and 17α-ethinylestradiol (EE2). General and Comparative Endocrinology, 167(1), 128-134. https://doi.org/10.1016/j.ygcen.2010.01.023"},
        ],
        "actual_records": {3: _rec("doc3_b1_3_berishvili")},
    },
    {
        "id": "doc1_a2_1_pereira_partial",
        "source": "Doc1 (ChatGPT) A2.1",
        "summary": "Fabricated author (Pereira 2022) + wrong year + wrong volume/artno over the real Naya-Català 2021 DOI; declared title is a shortened paraphrase (similarity 0.815).",
        "kind": "caught",
        "expected_checks": [10, 16],
        "unexpected_ok": [],
        "notes": "GAP for check_07: declared title similarity is 0.815 < 0.85, so check_07 abstains and the fabricated author goes unflagged. check_10 DOES fire on the wrong volume (12 vs 11). The wrong declared year (2022 vs the record's 2021) is now caught by check_16.",
        "sentences": [{"id": "s1", "text": "Tissue-specific differentiation of the GH/IGF system was shown by [1]."}],
        "body_text": "Tissue-specific differentiation of the GH/IGF system was shown by [1].",
        "references": [
            {"number": 1, "raw": "Pereira, B. F., et al. (2022). Transcriptomic profiling of GH/IGF system reveals tissue-specific differentiation in gilthead sea bream. Scientific Reports, 12(1), 24326. https://doi.org/10.1038/s41598-021-95408-6"},
        ],
        "actual_records": {1: _rec("doc1_a2_1_pereira")},
    },
    {
        "id": "doc1_a2_2_weng_zhong_gap",
        "source": "Doc1 (ChatGPT) A2.2",
        "summary": "Fabricated author (Weng 2022) over the real Zhong et al. DOI; declared title is a much shorter paraphrase (similarity 0.500).",
        "kind": "gap",
        "expected_checks": [],
        "notes": "check_07 abstains (0.500 < 0.85): the paraphrase is too far from the real title, so neither resolution's gate nor check_07 can confirm 'right paper' and the fabricated author slips through. Journal and volume (Frontiers in Immunology 13) happen to be right, so check_10 is silent too. Same mechanism as doc1_a2_1_pereira and doc3_b4_7_elizur / _30_zohar.",
        "sentences": [{"id": "s1", "text": "IGF-1 injection alters stomach gene expression in tilapia [1]."}],
        "body_text": "IGF-1 injection alters stomach gene expression in tilapia [1].",
        "references": [
            {"number": 1, "raw": "Weng, P.-C., et al. (2022). IGF-1 injection changes gene expression in tilapia stomach by RNA-seq. Frontiers in Immunology, 13, 959717. https://doi.org/10.3389/fimmu.2022.959717"},
        ],
        "actual_records": {1: _rec("doc1_a2_2_weng_zhong")},
    },
    {
        "id": "doc2_a4_1_naya_venus_caught",
        "source": "Doc2 (ChatGPT) A4.1",
        "summary": "Fabricated author 'Venús, M. I.' + wrong article number over the real Naya-Català DOI; title kept verbatim (similarity 1.000).",
        "kind": "caught",
        "expected_checks": [7, 10, 14],
        "notes": "With the title verbatim, check_07's ≥0.85 gate opens and the fabricated first author is caught -- the direct contrast with doc1_a2_1_pereira, where the same DOI appears with a paraphrased title and the author escapes. The catalog's wrong article number (22901 vs real 16466) is caught by check_10's pages comparison. check_14 fires on the silent author omission (2 declared, no 'et al.', 4 real).",
        "sentences": [{"id": "s1", "text": "RNA-seq profiling of the GH/IGF system was performed in [1]."}],
        "body_text": "RNA-seq profiling of the GH/IGF system was performed in [1].",
        "references": [
            {"number": 1, "raw": "Venús, M. I., & Pérez-Sánchez, J. (2021). Transcriptomic profiling of Gh/Igf system reveals a prompted tissue-specific differentiation and novel hypoxia responsive genes in gilthead sea bream. Scientific Reports, 11(1), 22901. https://doi.org/10.1038/s41598-021-95408-6"},
        ],
        "actual_records": {1: _rec("doc2_a4_1_naya_venus")},
    },
    {
        "id": "doc2_a4_2_nornberg_caught",
        "source": "Doc2 (ChatGPT) A4.2",
        "summary": "Fabricated authors (Marafon, Storozhenko) + wrong volume (225) and pages (48-56) over the real Nörnberg 2016 GCE 226:36-41; title verbatim.",
        "kind": "caught",
        "expected_checks": [7, 10],
        "notes": "check_07 catches Marafon; check_10 catches both the volume and the pages. Catalog confirms real author list is only Nornberg, Figueiredo, Marins.",
        "sentences": [{"id": "s1", "text": "IGF paralog expression in GH-transgenic zebrafish was profiled in [1]."}],
        "body_text": "IGF paralog expression in GH-transgenic zebrafish was profiled in [1].",
        "references": [
            {"number": 1, "raw": "Marafon, M., Storozhenko, S., et al. (2016). Expression profile of IGF paralog genes in liver and muscle of a GH-transgenic zebrafish. General and Comparative Endocrinology, 225, 48-56. https://doi.org/10.1016/j.ygcen.2015.12.017"},
        ],
        "actual_records": {1: _rec("doc2_a4_2_nornberg")},
    },
    {
        "id": "doc3_b4_7_elizur_gap",
        "source": "Doc3 (Gemini) B4 #7",
        "summary": "Fabricated author (Elizur) + fabricated title over the real Cohen-Rothschild 2024 DOI; title similarity 0.371.",
        "kind": "gap",
        "expected_checks": [],
        "notes": "Both identity fields fabricated together: title similarity is far below 0.85, so check_07 abstains. Matches the catalog's C2 pattern -- both models invent plausible authors (Elizur, Zohar) on real DOIs, and the module cannot see through it. Declared year is aligned to the real record (2024) so check_16 stays silent and the fixture isolates the actual gap (fabricated author + title), rather than being caught on a secondary year discrepancy.",
        "sentences": [{"id": "s1", "text": "A fast-growing transgenic zebrafish was characterized in [1]."}],
        "body_text": "A fast-growing transgenic zebrafish was characterized in [1].",
        "references": [
            {"number": 1, "raw": "Elizur, A., et al. (2024). Transgenic zebrafish expressing tilapia GH under the tilapia LH promoter. Frontiers in Endocrinology, 15, 1369043. https://doi.org/10.3389/fendo.2024.1369043"},
        ],
        "actual_records": {1: _rec("doc3_b4_7_elizur")},
    },
    {
        "id": "doc3_b4_30_zohar_gap",
        "source": "Doc3 (Gemini) B4 #30",
        "summary": "Fabricated author (Zohar 2024) + fabricated title over the real triploid crucian carp DOI; title similarity 0.590.",
        "kind": "gap",
        "expected_checks": [],
        "notes": "Same mechanism as doc3_b4_7_elizur_gap. Catalog: 'DOI correcto, autor y título inventados'.",
        "sentences": [{"id": "s1", "text": "GH/IGF axis expression in triploid crucian carp was analyzed in [1]."}],
        "body_text": "GH/IGF axis expression in triploid crucian carp was analyzed in [1].",
        "references": [
            {"number": 1, "raw": "Zohar, Y., et al. (2024). Polyploid breeding and GH/IGF axis regulation in allotriploid crucian carp hybrids. Frontiers in Endocrinology, 15, 1373623. https://doi.org/10.3389/fendo.2024.1373623"},
        ],
        "actual_records": {1: _rec("doc3_b4_30_zohar")},
    },
    {
        "id": "doc3_b4_6_doyle_partial",
        "source": "Doc3 (Gemini) B4 #6",
        "summary": "Declared 'Doyle, S. (2022)' with DOI 10.1016/j.ygcen.2009.04.020 whose year (2009) contradicts the declared year; real record is Tipsmark et al. 2009 GCE 163:270-277.",
        "kind": "caught",
        "expected_checks": [7, 14, 16],
        "notes": "check_07 fires: 'doyle' is not in the real author list (Tipsmark, Jørgensen, ...). check_14 also fires: a single declared author with no truncation marker vs 6 real authors is a silent omission. check_16 catches the declared-vs-actual year (2022 vs the record's 2009) -- the catalog's year/volume-DOI contradiction.",
        "sentences": [{"id": "s1", "text": "Cortisol effects on gill claudins in Atlantic salmon were examined in [1]."}],
        "body_text": "Cortisol effects on gill claudins in Atlantic salmon were examined in [1].",
        "references": [
            {"number": 1, "raw": "Doyle, S. (2022). Effects of cortisol, growth hormone and prolactin on gill claudin expression in Atlantic salmon. General and Comparative Endocrinology, 163(3), 270-277. https://doi.org/10.1016/j.ygcen.2009.04.020"},
        ],
        "actual_records": {1: _rec("doc3_b4_6_doyle")},
    },
    {
        "id": "doc3_b4_27_xie_partial",
        "source": "Doc3 (Gemini) B4 #27",
        "summary": "Declared 'Xie, Z., et al. (2001)' with DOI 10.1242/jeb.006890 (real J Exp Biol vol 211 = 2008); real record is Levesque et al. 2008.",
        "kind": "caught",
        "expected_checks": [7, 16],
        "notes": "check_07 fires (xie not in real list). check_16 catches the declared-vs-actual year (2001 vs the record's 2008).",
        "sentences": [{"id": "s1", "text": "Myogenesis in GH-transgenic Atlantic salmon was described in [1]."}],
        "body_text": "Myogenesis in GH-transgenic Atlantic salmon was described in [1].",
        "references": [
            {"number": 1, "raw": "Xie, Z., et al. (2001). Myogenesis and muscle metabolism in juvenile Atlantic salmon (Salmo salar) made transgenic for growth hormone. Journal of Experimental Biology, 211(1), 128-137. https://doi.org/10.1242/jeb.006890"},
        ],
        "actual_records": {1: _rec("doc3_b4_27_xie")},
    },

    # ======================================================================
    # CHECK 8 -- displaced / promoted authorship
    # ======================================================================
    {
        "id": "doc1_a2_3_doc3_12_lema_gap",
        "source": "Doc1 (ChatGPT) A2.3 = Doc3 (Gemini) B4 #12",
        "summary": "Both models independently promoted the LAST author (Lema, 2023) to first author of the Bersin et al. paper; title shortened (similarity 0.711).",
        "kind": "gap",
        "expected_checks": [],
        "notes": "check_08 compares each declared author only against OTHER references' actual author lists. Lema IS in this record's own actual list (as 6th author), so the promotion is invisible; and with a single reference there is no other list to cross-check. check_07 abstains too (lema is in the real list, and title similarity 0.711 < 0.85). The catalog flags this as the error both models repeated independently (C2) -- and it is entirely missed here.",
        "sentences": [{"id": "s1", "text": "Igf1 regulation of myogenesis was examined by [1]."}],
        "body_text": "Igf1 regulation of myogenesis was examined by [1].",
        "references": [
            {"number": 1, "raw": "Lema, S. C., et al. (2023). Nutritional status affects IGF1 regulation of skeletal muscle myogenesis in gopher rockfish. Molecular and Cellular Endocrinology, 573, 111951. https://doi.org/10.1016/j.mce.2023.111951"},
        ],
        "actual_records": {1: _rec("doc1_a2_3_doc3_12_lema_bersin")},
    },
    {
        "id": "doc2_a4_5_duran_displaced_caught",
        "source": "Doc2 (ChatGPT) A4.4 + A4.5",
        "summary": "Declared Duran 2022 entry carries 'Fernández-Borràs, J.' -- a real author, but of the Sánchez-Moya (cysteamine) paper, not of Duran's; Sánchez-Moya itself is listed with fabricated authors (Bermejo-Nogales, Kaufer, Sánchez).",
        "kind": "caught",
        "expected_checks": [7, 8, 14],
        "notes": "The catalog's note ('los nombres de autor sangraron de una referencia a otra') is exactly the displaced-authorship pattern check_08 exists for: Fernández-Borràs declared on [1] matches the resolved author list of [2] (Crossref spells it 'Fernández-Borràs', matching the catalog). check_07 fires on the fabricated first author of Sánchez-Moya (Bermejo-Nogales). check_14 also fires on both entries: each declares 3 authors with no truncation marker vs 7/11 real authors. Declared titles reconstructed as the real titles; the errors under test are the author lists.",
        "sentences": [{"id": "s1", "text": "Amino acids and IGF1 regulate fish muscle growth in vitro [1]; cysteamine improves the GH/IGF axis in sea bream [2]."}],
        "body_text": "Amino acids and IGF1 regulate fish muscle growth in vitro [1]; cysteamine improves the GH/IGF axis in sea bream [2].",
        "references": [
            {"number": 1, "raw": "Duran, G., Vinas, J., & Fernández-Borràs, J. (2022). Amino Acids and IGF1 Regulation of Fish Muscle Growth Revealed by Transcriptome and microRNAome Integrative Analyses of Pacu (Piaractus mesopotamicus) Myotubes. International Journal of Molecular Sciences, 23(3), 1180. https://doi.org/10.3390/ijms23031180"},
            {"number": 2, "raw": "Bermejo-Nogales, A., Kaufer, A., & Sánchez, N. (2023). Cysteamine improves growth and the GH/IGF axis in gilthead sea bream (Sparus aurata): in vivo and in vitro approaches. Frontiers in Endocrinology, 14, 1211470. https://doi.org/10.3389/fendo.2023.1211470"},
        ],
        "actual_records": {1: _rec("doc2_a4_5_duran"), 2: _rec("doc2_a4_4_sanchez_moya")},
    },

    # ======================================================================
    # CHECK 9 -- non-verbatim title
    # ======================================================================
    {
        "id": "doc2_a4_6_fuentes_title_gap",
        "source": "Doc2 (ChatGPT) A4.6",
        "summary": "Declared title tail altered ('by the GH-IGF endocrine system' instead of 'by the growth hormone - Insulin-like growth factor system'); similarity 0.759.",
        "kind": "gap",
        "expected_checks": [],
        "notes": "check_09's floor is 0.85: below it the check abstains (by design, that band is resolution's job). Here the altered tail drags similarity to 0.759, so the non-verbatim title is missed. Also below resolution's own 0.85 gate, so in a live run this reference would route to synthesized_title -- which is non-blocking and still lets the altered title through without a finding.",
        "sentences": [{"id": "s1", "text": "Fish muscle growth regulation is reviewed in [1]."}],
        "body_text": "Fish muscle growth regulation is reviewed in [1].",
        "references": [
            {"number": 1, "raw": "Fuentes, E. N., Valdés, J. A., Molina, A., & Björnsson, B. T. (2013). Regulation of skeletal muscle growth in fish by the GH-IGF endocrine system. General and Comparative Endocrinology, 192, 136-148. https://doi.org/10.1016/j.ygcen.2013.06.009"},
        ],
        "actual_records": {1: _rec("doc2_a4_6_fuentes")},
    },

    # ======================================================================
    # CHECK 10 -- field-by-field metadata
    # ======================================================================
    {
        "id": "doc3_b4_19_picha_pages_caught",
        "source": "Doc3 (Gemini) B4 #19",
        "summary": "Declared pages 167-183 vs real 196-211 (Picha 2008, NAJA 70(2)).",
        "kind": "caught",
        "expected_checks": [10],
        "notes": "check_10's pages comparison catches it.",
        "sentences": [{"id": "s1", "text": "Endocrine growth biomarkers in fish are reviewed by [1]."}],
        "body_text": "Endocrine growth biomarkers in fish are reviewed by [1].",
        "references": [
            {"number": 1, "raw": "Picha, M. E., Turano, M. J., Beckman, B. R., & Borski, R. J. (2008). Endocrine Biomarkers of Growth and Applications to Aquaculture: A Minireview of Growth Hormone, Insulin-Like Growth Factor (IGF)-I, and IGF-Binding Proteins as Potential Growth Indicators in Fish. North American Journal of Aquaculture, 70(2), 167-183. https://doi.org/10.1577/a07-038.1"},
        ],
        "actual_records": {1: _rec("doc3_b4_19_picha")},
    },
    {
        "id": "doc2_a4_3_serrana_volume_caught",
        "source": "Doc2 (ChatGPT) A4.3",
        "summary": "Declared (2019) Frontiers in Endocrinology 10, 80 vs real 2018, vol 9 (article 80).",
        "kind": "caught",
        "expected_checks": [10, 16],
        "unexpected_ok": [7],
        "notes": "check_10 catches the volume (10 vs 9) and check_16 catches the declared-vs-actual year (2019 vs the record's 2018). check_07 is a known module quirk here, tolerated via unexpected_ok: the declared 'García de la Serrana' (accented, as any document would write it) is compared by exact lowercase string against Crossref's unaccented 'Garcia de la Serrana', so the check spuriously fires on the accent difference -- a normalization gap, not the catalog's error.",
        "sentences": [{"id": "s1", "text": "IGFBPs in teleost fishes are reviewed by [1]."}],
        "body_text": "IGFBPs in teleost fishes are reviewed by [1].",
        "references": [
            {"number": 1, "raw": "García de la Serrana, D., & Macqueen, D. J. (2019). Insulin-Like Growth Factor-Binding Proteins of Teleost Fishes. Frontiers in Endocrinology, 10, 80. https://doi.org/10.3389/fendo.2018.00080"},
        ],
        "actual_records": {1: _rec("doc2_a4_3_serrana_macqueen")},
    },

    # ======================================================================
    # CHECK 11 -- volume within sampled range (synthetic, cache-injected)
    # ======================================================================
    {
        "id": "synthetic_check_11_volume_range",
        "source": "synthetic (capability)",
        "summary": "Declared volume 160 for GCE 2010, where sampled records bracket 2010 at volume ~167.",
        "kind": "synthetic",
        "expected_checks": [11],
        "notes": "Not from a catalog (the catalog's Koganti year/volume case abstains -- see doc8_koganti_year_gap). Uses an INLINE actual record whose volume matches the declared 160 so check_10 stays silent and the finding isolates check_11's bracketing logic. Injected journal_range_cache avoids a live Crossref call.",
        "sentences": [{"id": "s1", "text": "IGF-3 in tilapia gonads is described in [1]."}],
        "body_text": "IGF-3 in tilapia gonads is described in [1].",
        "references": [
            {"number": 1, "raw": "Berishvili, G., Baroiller, J.-F., Eppler, E., & Reinecke, M. (2010). Insulin-like growth factor-3 (IGF-3) in male and female gonads of the tilapia. General and Comparative Endocrinology, 160(1), 128-134. https://doi.org/10.1016/j.ygcen.2010.01.023"},
        ],
        "actual_records": {1: {
            "doi": "10.1016/j.ygcen.2010.01.023", "status": "resolved",
            "title": "Insulin-like growth factor-3 (IGF-3) in male and female gonads of the tilapia.",
            "authors": [{"family": "Berishvili", "given": "G."}, {"family": "Baroiller", "given": "J.-F."},
                        {"family": "Eppler", "given": "E."}, {"family": "Reinecke", "given": "M."}],
            "year": 2010, "journal": "General and Comparative Endocrinology",
            "volume": "160", "issue": "1", "pages": "128-134", "issn": "0016-6480",
        }},
        "journal_range_cache": {"0016-6480": [(2009, "166"), (2010, "167"), (2011, "168"), (2012, "169")]},
    },

    # ======================================================================
    # CHECK 15 -- retracted paper (client-requested; free Retraction Watch data)
    # ======================================================================
    {
        "id": "doc1_a2_6_retracted_caught",
        "source": "synthetic on verified record (Crossref Retraction Watch integration)",
        "summary": "A reference whose resolved record 10.1155/2021/5554487 is retracted -- Crossref's updated-by carries a type=retraction notice (10.1155/2024/9753878, source retraction-watch).",
        "kind": "caught",
        "expected_checks": [15],
        "unexpected_ok": [9],
        "notes": "Not from the error catalogs (the module never checked retraction before), but built on a LIVE-verified retracted record: the original DOI's record carries updated-by [{type: retraction, DOI: 10.1155/2024/9753878, source: retraction-watch}] injected by Crossref's free Retraction Watch integration. check_15 fires critical; the reference is miscited (non-blocking). check_09 is tolerated via unexpected_ok: the real record's title is prefixed '[Retracted] ' (that prefix IS the retraction marker), which drags similarity to 0.95 -- below check_09's 0.97 verbatim bar -- so the check spurs a non-verbatim-title finding. That is a quirk of the retraction marker, not a transcription error by the author.",
        "sentences": [{"id": "s1", "text": "Blockchain-based trial platforms were proposed by [1]."}],
        "body_text": "Blockchain-based trial platforms were proposed by [1].",
        "references": [
            {"number": 1, "raw": "Hang, L., Kim, B., Kim, K., & Kim, D. (2021). A Permissioned Blockchain-Based Clinical Trial Service Platform to Improve Trial Data Transparency. BioMed Research International, 2021(1), 5554487. https://doi.org/10.1155/2021/5554487"},
        ],
        "actual_records": {1: _rec("doc1_a2_6_retracted")},
    },

    # ======================================================================
    # CHECK 13 -- citation markers vs bibliography
    # ======================================================================
    {
        "id": "doc2_a5_orphan_nevercited",
        "source": "Doc2 (ChatGPT) A5",
        "summary": "Marker [75] cited in the body has no bibliography entry; entries [73], [10], [7] are listed but never cited.",
        "kind": "caught",
        "expected_checks": [13],
        "unexpected_ok": [5],
        "unexpected_ok": [8],
        "notes": "check_13 fires both directions: orphaned [75] (critical) and never-cited 7/10/73 (warning). Bibliography entries for the never-cited refs are minimal placeholders -- never-cited detection is purely structural. check_08 is tolerated as a documented precision limitation: ref [7] is the real Pérez-Sánchez entry, and its declared authors (Pérez-Sánchez, Calduch-Giner) also co-author the Naya-Català record (ref [1]); because ref [7] has no resolved record of its own, check_08 cannot distinguish legitimate shared authorship from displaced authorship and fires a false positive.",
        "sentences": [
            {"id": "s1", "text": "GHR2 is the most expressed receptor in skeletal and cardiac muscle [75]."},
            {"id": "s2", "text": "The GH/IGF axis was reviewed in [1]."},
        ],
        "body_text": "GHR2 is the most expressed receptor in skeletal and cardiac muscle [75]. The GH/IGF axis was reviewed in [1].",
        "references": [
            {"number": 1, "raw": N_A_Y},
            {"number": 7, "raw": "Pérez-Sánchez, J., & Calduch-Giner, J. (2002). Molecular basis of the GH/IGF axis in fish. Aquaculture, 20, 100."},
            {"number": 10, "raw": "Ndandala, C., et al. (2021). GH/IGF axis in aquaculture. Aquaculture Reports, 25, 101339."},
            {"number": 73, "raw": "Duran, G. (2021). Muscle growth in fish. Aquaculture, 30, 200."},
        ],
        "actual_records": {1: _rec("doc1_a2_1_pereira")},
    },
    {
        "id": "doc3_b1_orphaned_biblio",
        "source": "Doc3 (Gemini) B1",
        "summary": "The formal APA bibliography is entirely orphaned: the body uses markers 31, 35, 37, 38 that resolve nowhere in the 30-entry APA list.",
        "kind": "caught",
        "expected_checks": [13],
        "notes": "Represents the catalog's arithmetic proof (marks 31/35/37/38 vs 30 APA entries) on a 3-entry subset. check_13 flags every orphan marker plus every never-cited entry.",
        "sentences": [
            {"id": "s1", "text": "Sarcopenia and muscle aging are relevant to fish satellite cells [31]."},
            {"id": "s2", "text": "Lipid metabolism in human disease is tangential [35][37][38]."},
        ],
        "body_text": "Sarcopenia and muscle aging are relevant to fish satellite cells [31]. Lipid metabolism in human disease is tangential [35][37][38].",
        "references": [
            {"number": 1, "raw": "Berishvili, G., et al. (2010). IGF-3 in tilapia gonads. General and Comparative Endocrinology, 167(1), 128-134."},
            {"number": 2, "raw": "Cleveland, B. M., et al. (2020). IGFBP editing in rainbow trout. Scientific Reports, 10, 100."},
            {"number": 3, "raw": "Zhong, H., et al. (2022). IGF-1 injection in tilapia stomach. Frontiers in Immunology, 13, 959717."},
        ],
        "actual_records": {},
    },

    # ======================================================================
    # CHECK 14 -- author omission
    # ======================================================================
    {
        "id": "doc3_b1_29_zhong_omission_caught",
        "source": "Doc3 (Gemini) B4 / B1 #29",
        "summary": "Zhong et al. declared with 3 authors, no 'et al.' marker; the real record has 4 (Zhou Yi omitted).",
        "kind": "caught",
        "expected_checks": [14],
        "notes": "check_14_author_omission fires: declared count 3 < actual 4 with no truncation marker. First author (Zhong) is correct, so check_07 is silent -- the finding is purely the silent omission.",
        "sentences": [{"id": "s1", "text": "IGF-1 injection in tilapia stomach was studied by [1]."}],
        "body_text": "IGF-1 injection in tilapia stomach was studied by [1].",
        "references": [
            {"number": 1, "raw": "Zhong, H., Lou, C., & Ren, B. (2022). Insulin-like growth factor 1 injection changes gene expression related to amino acid transporting, complement and coagulation cascades in the stomach of tilapia revealed by RNA-seq. Frontiers in Immunology, 13, 959717. https://doi.org/10.3389/fimmu.2022.959717"},
        ],
        "actual_records": {1: _rec("doc3_b1_29_zhong")},
    },
    {
        "id": "synthetic_check_14_malformed_truncation",
        "source": "synthetic (capability)",
        "summary": "An 'et al.' truncation marker with no named author before it -- malformed on its face.",
        "kind": "synthetic",
        "expected_checks": [14],
        "notes": "Phase-A half of check 14: a truncation marker with zero named authors is flagged without needing the real record.",
        "sentences": [{"id": "s1", "text": "The axis was reviewed in [1]."}],
        "body_text": "The axis was reviewed in [1].",
        "references": [
            {"number": 1, "raw": "et al. (2023). A review of the GH/IGF axis. Journal of Fish Biology, 10(1), 100-120."},
        ],
        "actual_records": {},
    },

    # ======================================================================
    # Doc8 -- structural cases the module cannot represent (GAP)
    # ======================================================================
    {
        "id": "doc8_ambiguous_author_year_keys",
        "source": "Doc8 (Consensus)",
        "summary": "Author-year citation keys ('Fuentes 2013', 'Fuentes 2011', 'Vélez 2017') that resolve ambiguously -- the exact ambiguity the original check 1 protected against.",
        "kind": "gap",
        "expected_checks": [],
        "notes": "check_01 was REPURPOSED for the numbered+DOI format (duplicate DOI under two numbers) and cannot see author-year keys at all. Represented as three distinct numbered references: no duplicate DOI exists, so no finding -- the ambiguity is invisible.",
        "sentences": [
            {"id": "s1", "text": "Muscle growth regulation is discussed in [1] and [2]."},
            {"id": "s2", "text": "Salinity effects on the axis are covered in [3]."},
        ],
        "body_text": "Muscle growth regulation is discussed in [1] and [2]. Salinity effects on the axis are covered in [3].",
        "references": [
            {"number": 1, "raw": "Fuentes, E. N., et al. (2013). Regulation of skeletal muscle growth in fish. General and Comparative Endocrinology, 192, 136-148."},
            {"number": 2, "raw": "Fuentes, E. N., et al. (2011). Muscle growth regulation in fish. Journal of Fish Biology, 78, 100-115."},
            {"number": 3, "raw": "Vélez, E. J., et al. (2017). Salinity and the GH/IGF axis. Scientific Reports, 7, 50."},
        ],
        "actual_records": {},
    },
    {
        "id": "doc8_koganti_year_gap",
        "source": "Doc8 (Consensus)",
        "summary": "Koganti cited as (2020) in Animals, but volume 11 of Animals is 2021 -- the real record is displaced ~1 year (catalog: '6 years displaced', online-first).",
        "kind": "gap",
        "expected_checks": [],
        "notes": "check_16 covers this exact pattern (declared 2020 vs a real 2021 record WOULD fire) -- but here the real record was never located (declared-only fixture, no stable DOI), so nothing can fire. The remaining gap is record resolution, not the year comparison. Volume 11 is internally plausible for 2020, so check_11 also abstains.",
        "sentences": [{"id": "s1", "text": "GH and IGFBP regulation was studied by [1]."}],
        "body_text": "GH and IGFBP regulation was studied by [1].",
        "references": [
            {"number": 1, "raw": "Koganti, P. P., et al. (2020). Growth hormone receptor and insulin-like growth factor binding protein responses in fish. Animals, 11(4), 1046."},
        ],
        "actual_records": {},
    },
    {
        "id": "doc5_structural_never_cited",
        "source": "Doc5 (SciSpace/Claude)",
        "summary": "Executable Doc5 coverage: two bibliography entries are never cited in the body.",
        "kind": "caught",
        "expected_checks": [13],
        "unexpected_ok": [5],
        "notes": "This fixture converts the documented Doc5 never-cited-reference finding into an executable structural case.",
        "sentences": [{"id": "s1", "text": "The review cites the first paper [1]."}],
        "body_text": "The review cites the first paper [1].",
        "references": [
            {"number": 1, "raw": "Faught, E. (2018). A real paper. Journal, 1, 1."},
            {"number": 2, "raw": "Das, A. (2023). An uncited paper. Journal, 2, 2."},
            {"number": 3, "raw": "Vijayan, M. (2019). Another uncited paper. Journal, 3, 3."},
        ],
        "actual_records": {},
    },
    {
        "id": "doc7_partition_range_error",
        "source": "Doc7 (Elicit)",
        "summary": "Executable Doc7 coverage: a partition count does not match the expanded citation ranges.",
        "kind": "caught",
        "expected_checks": [2],
        "notes": "The fixture preserves the documented five/four/two partition and [1-3] range mismatch.",
        "sentences": [{"id": "s1", "text": "Five studies [5,6,8-10]. Four studies [1-3]. Two studies [4,7]."}],
        "body_text": "Five studies [5,6,8-10]. Four studies [1-3]. Two studies [4,7].",
        "references": [{"number": i, "raw": "Author%d, A. (2020). Study %d. Journal, %d, %d." % (i, i, i, i)} for i in range(1, 11)],
        "actual_records": {},
    },
    {
        "id": "doc9_displaced_authorship_reconstructed",
        "source": "Doc9 (Grok)",
        "summary": "Reconstructed Doc9 Macedo/Fappi and Fiorenza/Macedo authorship displacement case.",
        "kind": "caught",
        "expected_checks": [7, 8, 14],
        "notes": "The fixture is reconstructed from the concrete Doc9 finding: declared authors are assigned across the two resolved records. It is not claimed to be a preserved raw Crossref execution.",
        "sentences": [{"id": "s1", "text": "The two studies are listed in [11] and [14]."}],
        "body_text": "The two studies are listed in [11] and [14].",
        "references": [
            {"number": 11, "raw": "Macedo, A. G. (2020). Paper one. Journal, 1, 1."},
            {"number": 14, "raw": "Fiorenza, M. (2021). Paper two. Journal, 2, 2."},
        ],
        "actual_records": {
            11: {"doi": "10.5555/doc9.one", "status": "resolved", "title": "Paper one", "authors": [{"family": "Fappi", "given": "F."}, {"family": "Fiorenza", "given": "M."}], "year": 2020, "journal": "Journal", "volume": "1", "pages": "1"},
            14: {"doi": "10.5555/doc9.two", "status": "resolved", "title": "Paper two", "authors": [{"family": "Macedo", "given": "A. G."}], "year": 2021, "journal": "Journal", "volume": "2", "pages": "2"},
        },
    },
    {
        "id": "doc10_container_substitution",
        "source": "Doc10 (Perplexity) reconstructed",
        "summary": "Reconstructed Doc10 capability fixture for journal-container substitution.",
        "kind": "synthetic",
        "expected_checks": [10],
        "notes": "The DOI and declared authors/title are kept aligned so this fixture isolates journal/volume/pages substitution. The available Doc10 report does not include a preserved executable Crossref record for this exact case, so this is not counted as real-record Doc10 coverage.",
        "sentences": [{"id": "s1", "text": "The study is listed in [1]."}],
        "body_text": "The study is listed in [1].",
        "references": [{"number": 1, "raw": "Naya-Català, F., Simó-Mirabet, P., Calduch-Giner, J., & Pérez-Sánchez, J. (2021). Transcriptomic profiling of Gh/Igf system reveals a prompted tissue-specific differentiation and novel hypoxia responsive genes in gilthead sea bream. Communications Biology, 99(1), 1-2. https://doi.org/10.1038/s41598-021-95408-6"}],
        "actual_records": {1: _rec("doc1_a2_1_pereira")},
    },
]

# Catalog cases documented as GAPS that cannot be turned into runnable
# fixtures (no stable identifier / out of the module's data model). Surfaced
# in the harness report so they are not silently forgotten.
DOCUMENTED_GAPS = [
    {
        "id": "doc9_wrong_article_identifiers_gap",
        "source": "Doc9 (Grok) check 7, refs 3/6/10/16",
        "summary": "Four documented Doc9 DOI identifiers resolve to papers other than the works declared in the bibliography.",
        "notes": "Non-runnable in the current offline harness: the original resolved Crossref records and declared metadata needed to reproduce the wrong-article resolution path are not preserved in this package. A filler record can trigger unrelated field-comparison checks (for example check 10) and would not validate the documented wrong-article finding, so no executable PASS fixture is manufactured here.",
    },
    {
        "id": "doc8_truncated_locators",
        "source": "Doc8 (Consensus)",
        "summary": "Truncated locators: e.g. Mapes' entry missing its volume/article '361:114659'; book-chapter format (Sadoul & Vijayan) not parseable by the APA-tail parser.",
        "notes": "check_10 only fires when the declared field EXISTS (None vs actual is 'cannot compare', not a mismatch), and the parser leaves unparseable locators as None -- so a truncated locator is structurally invisible.",
    },
    {
        "id": "doc3_b2_off_domain_sources",
        "source": "Doc3 (Gemini) B2",
        "summary": "Source pool polluted with non-papers / human-medicine references: KAKEN researcher fiche (#14), congress abstract book with corrupt label (#34), human sarcopenia reviews (#18/#31), human intrauterine growth retardation (#25), human GH/IGF children (#39), human lipid metabolism (#9/#11), and a formal bibliography linking to Sci-Hub (#18).",
        "notes": "No check inspects domain relevance of a resolved record; the module assumes the bibliography is on-topic and only verifies metadata identity.",
    },
    {
        "id": "doc3_b4_11_wrong_pmc_url",
        "source": "Doc3 (Gemini) B4 #11",
        "summary": "Declared 'Lai, F., et al. (2025). Ghrelin is involved in voluntary anorexia in Atlantic salmon' whose URL (PMC12380893) actually points to a different paper ('Influence of water temperature on feed intake...').",
        "notes": "A URL-vs-content mismatch. The module has no PM-ID resolution or URL-content verification; a wrong PMC link reads as just another link.",
    },
    {
        "id": "doc1_a2_5_doc2_a3_2_orphan_claims",
        "source": "Doc1 A2.5 / Doc2 A3.2",
        "summary": "Content-level misattributions where the cited marker is either orphaned (Doc1 【90】【47】) or points to a reference that does not contain the claim (Doc2 【75】) -- the claim text is the problem, not the marker's existence.",
        "notes": "check_13 catches an orphaned MARKER as a structural error (it does fire for 【90】【47】 style orphans), but cannot know the claim is absent from the cited paper -- that needs content comparison, out of the metadata module's scope.",
    },
    {
        "id": "doc2_a2_7_cross_document_inconsistency",
        "source": "Doc1/Doc2 (ChatGPT) A2.7",
        "summary": "The Ndandala reference appears as Aquaculture Reports 25:101339 in Doc1 and 26:101289 in Doc2 -- one of the two is wrong, but each document is internally consistent.",
        "notes": "Requires cross-document comparison; the module processes one document per run.",
    },
]
