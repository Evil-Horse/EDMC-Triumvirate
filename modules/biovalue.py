"""
Модуль BioValue: подсчёт стоимости биологических образцов (экзобиология).

Использует данные самого плагина:
  * словари названий видов из modules.bio_dicts;
  * журнальные события, которые уже приходят через JournalEntryProcessor;
  * приоритет локации от BioPatrol (priority == 3 -> "первый в галактике" -> бонус x5).
Недостающих данных (цены видов) в плагине нет, поэтому таблица цен лежит здесь.
"""
import json
import tkinter as tk
from pathlib import Path
from threading import RLock

from modules.bio_dicts import codex_to_english_variants, codex_to_english_genuses
from modules.debug import debug
from modules.lib.context import global_context
from modules.lib.journal import JournalEntry
from modules.lib.module import Module

# Приблизительная базовая стоимость видов (кредиты). Можно править.
SPECIES_VALUES = {
    "Aleoida Arcus": 7252500, "Aleoida Coronamus": 6284600, "Aleoida Gravis": 12934900,
    "Aleoida Laminiae": 3385200, "Aleoida Spica": 3385200,
    "Amphora Plant": 1628800,
    "Bacterium Acies": 1000000, "Bacterium Alcyoneum": 1658500, "Bacterium Aurasus": 1000000,
    "Bacterium Bullaris": 1152500, "Bacterium Cerbrus": 1689800, "Bacterium Informem": 8418000,
    "Bacterium Nebulus": 5289900, "Bacterium Omentum": 4638900, "Bacterium Scopulum": 4934500,
    "Bacterium Tela": 1949000, "Bacterium Verrata": 3897000, "Bacterium Vesicula": 1000000,
    "Bacterium Volu": 7774700,
    "Cactoida Cortexum": 3667600, "Cactoida Lapis": 2483600, "Cactoida Peperatis": 2483600,
    "Cactoida Pullulanta": 3667600, "Cactoida Vermis": 16202800,
    "Clypeus Lacrimam": 8418000, "Clypeus Margaritus": 11873200, "Clypeus Speculumi": 16202800,
    "Concha Aureolas": 7774700, "Concha Biconcavis": 19010800, "Concha Labiata": 2352400,
    "Concha Renibus": 4572400,
    "Electricae Pluma": 6284600, "Electricae Radialem": 6284600,
    "Fonticulua Campestris": 1000000, "Fonticulua Digitos": 1804100, "Fonticulua Fluctus": 20000000,
    "Fonticulua Lapida": 3111000, "Fonticulua Segmentatus": 19010800, "Fonticulua Upupam": 5727600,
    "Frutexa Acus": 7774700, "Frutexa Collum": 1639800, "Frutexa Fera": 1632500,
    "Frutexa Flabellum": 1808900, "Frutexa Flammasis": 10326000, "Frutexa Metallicum": 1632500,
    "Frutexa Sponsae": 5988000,
    "Fumerola Aquatis": 6284600, "Fumerola Carbosis": 6284600, "Fumerola Extremus": 16202800,
    "Fumerola Nitris": 7500900,
    "Fungoida Bullarum": 3703200, "Fungoida Gelata": 3330300, "Fungoida Setisis": 1670100,
    "Fungoida Stabitis": 2680300,
    "Osseus Cornibus": 1483000, "Osseus Discus": 12934900, "Osseus Fractus": 4029700,
    "Osseus Pellebantus": 9739000, "Osseus Pumice": 3156300, "Osseus Spiralis": 2404700,
    "Recepta Conditivus": 14313700, "Recepta Deltahedronix": 16202800, "Recepta Umbrux": 12934900,
    "Stratum Araneamus": 2448900, "Stratum Cucumisis": 16202800, "Stratum Excutitus": 2448900,
    "Stratum Frigus": 2637500, "Stratum Laminamus": 2788300, "Stratum Limaxus": 1362000,
    "Stratum Paleas": 1362000, "Stratum Tectonicas": 19010800,
    "Tubus Cavas": 11873200, "Tubus Compagibus": 7774700, "Tubus Conifer": 2415500,
    "Tubus Rosarium": 2637500, "Tubus Sororibus": 5727600,
    "Tussock Albata": 3252500, "Tussock Capillum": 7025800, "Tussock Caputus": 3472400,
    "Tussock Catena": 1766600, "Tussock Cultro": 1766600, "Tussock Divisa": 1766600,
    "Tussock Ignis": 1849000, "Tussock Pennata": 5853800, "Tussock Pennatis": 1000000,
    "Tussock Propagito": 1000000, "Tussock Serrati": 4447100, "Tussock Stigmasis": 19010800,
    "Tussock Triticum": 7774700, "Tussock Ventusa": 3277700, "Tussock Virgam": 14313700,
    # Виды без названия рода в словаре bio_dicts (приблизительно)
    "Amphora Plant": 1628800, "Bark Mounds": 1471900,
    "Roseum Brain Tree": 1593700, "Gypseeum Brain Tree": 1593700, "Ovatum Brain Tree": 3565100,
    "Puniceum Brain Tree": 1593700, "Lindigoticum Brain Tree": 1593700, "Viride Brain Tree": 1593700,
    "Puniceum Anemone": 1499900, "Prasinum Bioluminescent Anemone": 1499900,
    "Luteolum Anemone": 1499900, "Croceum Anemone": 3399800, "Roseum Anemone": 1499900,
    "Blatteum Bioluminescent Anemone": 1499900, "Rubeum Bioluminescent Anemone": 1499900,
    "Roseum Bioluminescent Anemone": 1499900, "Prasinum Anemone": 1499900,
}

FIRST_BONUS = 5          # множитель за первое открытие
PRIORITY_FIRST = 3       # приоритет BioPatrol "ПЕРВЫЙ В ГАЛАКТИКЕ"
STATE_FILE = "biovalue.json"


def fmt(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def resolve_species(data: dict):
    """Определяет название вида ('Genus Species') по событию ScanOrganic."""
    name = data.get("Species_Localised")
    if name:
        return name
    for key in (data.get("Variant"), data.get("Species")):
        if key and key in codex_to_english_variants:
            return codex_to_english_variants[key].split(" - ")[0]
    genus = data.get("Genus_Localised") or codex_to_english_genuses.get(data.get("Genus"), data.get("Genus"))
    return genus or "Unknown"


class BioValue(tk.Frame, Module):
    def __init__(self, parent, gridrow):
        super().__init__(parent)
        self._lock = RLock()
        self.pending = {}      # timestamp -> {"name", "variant", "body", "first"}
        self.sold = {}         # timestamp продажи -> сумма (защита от повторов при перечитывании журнала)
        self.manual_first = tk.IntVar(value=0)
        self.biopatrol = None  # подставляется в on_start

        self.label_pending = tk.Label(self, anchor="w", justify="left")
        self.label_sold = tk.Label(self, anchor="w", justify="left")
        self.check = tk.Checkbutton(self, text="Все образцы: бонус x5", variable=self.manual_first,
                                    command=self._refresh)
        self.label_pending.grid(row=0, column=0, sticky="w")
        self.label_sold.grid(row=1, column=0, sticky="w")
        self.check.grid(row=2, column=0, sticky="w")
        self.columnconfigure(0, weight=1)
        self.grid(column=0, row=gridrow, sticky="NWSE")
        self._load_state()
        self._refresh()

    # ---------- хуки Module ----------
    def on_start(self, plugin_dir):
        # ищем BioPatrol среди модулей, чтобы читать его приоритеты
        for mod in global_context.modules:
            if hasattr(mod, "get_priority"):
                self.biopatrol = mod
                break

    def on_journal_entry(self, entry: JournalEntry):
        ev = entry.data.get("event")
        changed = False
        with self._lock:
            if ev == "ScanOrganic" and entry.data.get("ScanType") == "Analyse":
                ts = entry.data.get("timestamp")
                if ts not in self.pending:
                    name = resolve_species(entry.data)
                    body = entry.body
                    priority = 1
                    if self.biopatrol is not None:
                        try:
                            variant = codex_to_english_variants.get(entry.data.get("Variant"), "")
                            priority = max(self.biopatrol.get_priority(variant, body),
                                           self.biopatrol.get_priority(name, body))
                        except Exception:
                            debug("BioValue: не удалось получить приоритет BioPatrol")
                    self.pending[ts] = {
                        "name": name,
                        "variant": entry.data.get("Variant_Localised", ""),
                        "body": body,
                        "first": priority >= PRIORITY_FIRST,
                    }
                    changed = True
            elif ev == "SellOrganicData":
                ts = entry.data.get("timestamp")
                self.sold[ts] = sum(i.get("Value", 0) + i.get("Bonus", 0)
                                    for i in entry.data.get("BioData", []))
                self.pending.clear()
                changed = True
            elif ev == "Died" and self.pending:
                self.pending.clear()
                changed = True
            if changed:
                self._save_state()
        if changed:
            self.after(0, self._refresh)   # обновляем UI из главного потока

    # ---------- расчёт ----------
    def pending_value(self):
        manual = bool(self.manual_first.get())
        total, unknown = 0, 0
        with self._lock:
            for p in self.pending.values():
                base = SPECIES_VALUES.get(p["name"])
                if base is None:
                    unknown += 1
                    continue
                total += base * (FIRST_BONUS if (manual or p["first"]) else 1)
            return total, unknown, len(self.pending)

    def _refresh(self):
        total, unknown, count = self.pending_value()
        text = f"Биота (не продано): {count} шт., ~{fmt(total)} CR"
        if unknown:
            text += f" (+{unknown} без цены)"
        self.label_pending["text"] = text
        self.label_sold["text"] = f"Продано за сессию: {fmt(sum(self.sold.values()))} CR"

    # ---------- сохранение между запусками ----------
    def _state_path(self):
        return Path(global_context.plugin_dir, "data", STATE_FILE)

    def _load_state(self):
        try:
            with open(self._state_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            self.pending = data.get("pending", {})
        except Exception:
            self.pending = {}

    def _save_state(self):
        try:
            path = self._state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"pending": self.pending}, f, ensure_ascii=False)
        except Exception:
            debug("BioValue: не удалось сохранить состояние")
