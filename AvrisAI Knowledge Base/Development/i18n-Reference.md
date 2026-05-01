# i18n Reference — RU / TJ / EN

Source: `index.html`, the `TR` object and `applyLang()` function. Adopted in Session 3 (`718baa7` → `d1e1f7a`, 2026-04-30).

## Mechanism

Three attribute conventions on HTML elements:

| Attribute       | Effect                                               |
|-----------------|------------------------------------------------------|
| `data-i18n="key"`   | Sets `el.textContent = TR[lang][key]`            |
| `data-phk="key"`    | Sets `el.placeholder = TR[lang][key]`            |
| `data-i18n-o="key"` | Sets `el.textContent` for `<option>` items       |

`applyLang()` walks all three, then re-renders dynamic screens that build their own DOM:
```
buildLangBtns(); buildPatList(); applyFilters(); updateDash();
renderWards(); renderHist(); renderICU();
```

For **data-driven strings** (history summaries, ICU diagnoses & notes), each record carries a key field that is resolved at render time:

```js
var histData = [{
  time: "08:40", patient: "Иванова А.М.",
  type: "soap",
  sumK: "hs_soap_exp",                      // key
  summary: "SOAP экспортирована в систему." // fallback
}, …];

var icuPats = [{
  name: "Рахимов С.К.",
  diagK: "icud_mi", diag: "Инфаркт миокарда, кардиогенный шок",
  notesK: "icun_vent_vaso", notes: "ИВЛ, вазопрессоры. Гемодинамика нестабильна.",
  …
}, …];
```

If the key resolves, use the translation; otherwise fall back to the raw text. This keeps user-generated entries (saved SOAPs, night-round notes) working without translations.

## What is NOT translated

These are kept identical across languages by design:

- Patient names: "Иванова А.М.", "Омаров Р.Б.", "Рахимов С.К." …
- Bed labels: "ОРИТ-1", "ПР-3"
- Department / ward: "Кардиология A1", "Пульмонология B3"
- Medical abbreviations: **SOAP**, **S** / **O** / **A** / **P**, **AI**, **STT**, **BMI**, **SpO₂**

## Key inventory by screen

### Sidebar
`nav_main · nav_dashboard · nav_consult · nav_night · nav_data · nav_history · nav_icu · nav_sys · nav_settings`

### Login
`login_tagline · login_forgot · login_title · login_sub · login_desc · login_f1 · login_f2 · login_f3 · lbl_email · lbl_pass · btn_login`

### Topbar / sidebar-footer
`user_name · user_role`

### Dashboard hero (cmd-bar)
`hero_work · hero_pat · hero_crit · hero_att · hero_avg · m_24h · m_acc · m_rec · m_upd`

### Dashboard stat cards
`m_soap_n · m_24h · m_sess · m_sess_d · m_acc · m_rec`

### Filters / patient list / Avris Score
`ph_search · f_all_d · f_all_w · btn_reset · pat_title · pat_empty · avris_score · lbl_best · lbl_watch · st_stab · st_watch · st_crit · f_pat`

### Activity timeline
`act_title · act_round · act_b3 · act_export`

### Consultation
`cons_rec · lbl_patient · lbl_transcript · tr_ph · btn_clear · btn_save · soap_title · ph_complaints · ph_exam · ph_diag · ph_plan · btn_soap · rec_press · rec_start · rec_stop · rec_recording · rec_words`

### Patient context (consultation top card)
`m_diag · m_allergy`

### History
`hist_title · hist_search_ph · hist_all · hist_round · hist_export · hist_today · hist_yest · hist_earlier · hist_today_up · hist_yest_up · hist_earlier_up · hist_open`

History summaries (data-driven via `sumK`):
`hs_soap_exp · hs_pdf · hs_round_b3 · hs_plan_upd · hs_followup · hs_spiro`

### Night Round
`night_title · night_badge · night_voice · btn_vfill · lbl_exam · lbl_plan · ph_vfill · ph_treat · btn_sround · w_label`

### ICU screen
`icu_title · icu_sub · icu_sec · post_sec · icu_total · icu_crit_n · icu_avg_spo2 · icu_call_doc · icu_days · icu_days_short · icu_attending · icu_upd · icu_doc`

ICU vitals labels & units:
`vital_hr · vital_hr_u · vital_bp · vital_bp_u · vital_spo2_u · vital_resp · vital_resp_u · vital_temp_u`

ICU diagnoses (via `diagK`):
`icud_mi · icud_pneum_ards · icud_postop_perit · icud_post_mi · icud_chole`

ICU notes (via `notesK`):
`icun_vent_vaso · icun_simv · icun_drain · icun_transferred · icun_activation`

ICU statuses (short forms — these populate the colored badge on each card):
`icu_critical · icu_serious · icu_stable`

| Key            | RU      | TJ     | EN       |
|----------------|---------|--------|----------|
| `icu_critical` | Крит.   | Танқ.  | Critical |
| `icu_serious`  | Тяжёл.  | Вазнин | Serious  |
| `icu_stable`   | Стаб.   | Устув. | Stable   |

### Settings — tabs
`m_profile · notif_title · set_lang_short · lbl_theme · set_about_t`

### Settings — profile pane
`set_acc · user_name · user_role · st_active · set_name_l · set_name_d · set_spec_l · set_spec_d · set_email_l · set_email_d`

### Settings — notifications pane
`set_push_l · set_push_d · set_sound_l · set_sound_d · set_emailrep_l · set_emailrep_d · set_dnd_l · set_dnd_d`

### Settings — language pane
`lbl_lang · set_lang_d · set_stt_l · set_stt_d`

### Settings — theme pane
`set_appear · lbl_theme · th_dark · th_light · set_compact_l · set_compact_d`

### Settings — about pane
`set_about_t · set_ver_l · set_stt_eng_l · set_lic_l · btn_logout`

### Patient modal
`pm_overview · pm_history · pm_last_soap · pm_no_records · pm_start_exam · m_diag · m_allergy · m_meds · m_hist · m_vitals · lbl_age · lbl_gender · lbl_height · lbl_weight · lbl_blood`

### Toasts / confirms / misc
`t_soap · t_soap_e · t_filled · t_round · t_ward · t_exam · btn_cancel · btn_yes · c_logout · c_logout_rec`

---

## STT (recognition) language switcher

In Consultation, the Whisper-language toggle (RU / TJ / EN) is **independent** from the UI language:

- Doctor can have UI in RU and dictate in TJ.
- Active button updates two things:
  1. `recBadge` — "Whisper AI · {Распознавание | Шинохт | Recognition}"
  2. Transcript placeholder — `TR[recLang].tr_ph`
- The recognition button uses `recLang` (local var), not the global UI `lang`.

---

## Common gotchas

1. **Adding a new translatable string:** add `data-i18n="my_key"` in HTML, then add `my_key:"…"` to all three of `TR.ru`, `TR.tj`, `TR.en`. Forgetting one language = empty/undefined string at runtime.
2. **JS-rendered DOM** (e.g., `renderHist`, `renderICUGrid`, `buildPatList`) — use `t("key")` directly. After changing `lang`, `applyLang()` re-runs all renderers.
3. **Object literal commas** — `TR.en` once contained `icu_critical:"Critical",,lbl_age:…` (double comma). It silently broke the entire `TR.en` object, leaving `lang` and `icuPats` undefined and freezing the UI in RU. Linter recommended.
4. **UPPERCASE vs lowercase forms** — for visually-uppercased section titles (CSS `text-transform: uppercase`) we sometimes keep the storage value lowercase. But for elements without that CSS rule (the History group titles `СЕГОДНЯ / ВЧЕРА / РАНЕЕ`), separate `_up` keys exist.
5. **Trailing arrows in localized text** — keys like `pm_start_exam:"Начать осмотр →"` and `hist_open:"Открыть →"` keep the arrow character inside the translation, not as separate JSX/HTML, so RTL-style direction does not apply (we have no RTL languages currently).
