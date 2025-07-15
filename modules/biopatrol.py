import gzip
import json
import tkinter as tk
from tkinter import ttk
from datetime import datetime
from pathlib import Path
from math import sqrt
from threading import Lock
from time import sleep
from typing import Callable, Any

from .debug import debug
from modules.lib.module import Module
from modules.lib.journal import JournalEntry, Coords
from modules.patrol.patrol_module import copyclip
from modules.lib.context import global_context
from modules.lib.thread import BasicThread
from modules.lib.conf import config as plugin_config

import myNotebook as nb     # type: ignore
from theme import theme     # type: ignore
from modules.bio_dicts import codex_to_english_variants, codex_to_english_genuses, regions

from modules.legacy import Reporter, URL_GOOGLE


def distance_between(a: Coords, b: Coords):
    dx = a.x - b.x
    dy = a.y - b.y
    dz = a.z - b.z
    return sqrt(dx**2 + dy**2 + dz**2)


def get_priority_text(priority: int):
    priority_text = None
    match priority:
        case 3: priority_text = "ПЕРВЫЙ В ГАЛАКТИКЕ"
        case 2: priority_text = "Открытие региона"
        case _: priority_text = ""
    return priority_text


class RegionFilterWindow(tk.Toplevel):
    CONFIG_KEY = "BioPatrol.regions_config"

    def __init__(self, parent: tk.Misc, callback: Callable[[dict[str, bool]], Any]):
        super().__init__(parent)
        self.callback = callback
        config = self.load_config()
        self.config = {r: tk.BooleanVar(value=config[r]) for r in regions}

        self.frame = ttk.Frame(self)

        # 42 региона: 3 столбца по 11, 1 на 9
        for i, (region, var) in enumerate(self.config.items()):
            column = int(i / 11)
            row = i % 11
            nb.Checkbutton(
                self.frame, variable=var, text=region,
                command=self.__change_save_button_state
            ).grid(column=column, row=row, sticky="W")

        self.set_all_button = nb.Button(self.frame, text="Выбрать все", command=self.__set_all)
        self.unset_all_button = nb.Button(self.frame, text="Снять все", command=self.__unset_all)
        self.save_button = nb.Button(self.frame, text="Сохранить")
        self.save_button.bind("<Button-1>", self.__save_config)
        self.set_all_button.grid(row=12, column=0, sticky="NWSE")
        self.unset_all_button.grid(row=12, column=1, sticky="NWSE")
        self.save_button.grid(row=12, column=3, sticky="NWSE")

        self.frame.pack()


    @classmethod
    def load_config(cls) -> dict[str, bool]:
        config = plugin_config.get_str(cls.CONFIG_KEY)
        if config is None:
            config = {r: True for r in regions}
            plugin_config.set(cls.CONFIG_KEY, json.dumps(config, ensure_ascii=False))
        else:
            config = json.loads(config)
        return config

    def __set_all(self):
        for _, var in self.config.items():
            var.set(True)
        self.save_button.configure(state="enabled")

    def __unset_all(self):
        for _, var in self.config.items():
            var.set(False)
        self.save_button.configure(state="disabled")

    def __change_save_button_state(self):
        self.save_button.configure(state="disabled" if not any(var.get() for _, var in self.config.items()) else "normal")

    def __save_config(self, event):
        if str(event.widget["state"]) == tk.DISABLED:
            return
        config = {reg: var.get() for reg, var in self.config.items()}
        plugin_config.set(self.CONFIG_KEY, json.dumps(config, ensure_ascii=False))
        self.callback(config)


class BioPatrol(tk.Frame, Module):
    FILENAME_RAW = 'bio.json.gz'
    FILENAME_FLAT = 'bio-flat.json'
    FILENAME_BIO = 'bio-found.json'

    def __init__(self, parent, gridrow):
        super().__init__(parent)

        self.plugin_dir = global_context.plugin_dir
        self.data: list[dict] = []
        self.enabled_regions: list[str] = [region for region, enabled in RegionFilterWindow.load_config().items() if enabled]
        self.current_coords: Coords = None
        self._enabled = False
        self.__threadlock = Lock()
        self.__region_filter_window: RegionFilterWindow = None
        self.__pos = 0
        self.__priority = 0
        self.__selected_bio = ""
        self.pinned_bio: str = None
        self.cmdr = None
        self.signals_in_system = {}

        self.IMG_PREV = tk.PhotoImage(file=Path(self.plugin_dir, "icons", "left_arrow.gif"))
        self.IMG_NEXT = tk.PhotoImage(file=Path(self.plugin_dir, "icons", "right_arrow.gif"))
        self.IMG_PIN = tk.PhotoImage(file=Path(self.plugin_dir, "icons", "pin.gif"))
        self.IMG_PINNED = tk.PhotoImage(file=Path(self.plugin_dir, "icons", "pinned.gif"))
        self.IMG_TO_BEGINNING = tk.PhotoImage(file=Path(self.plugin_dir, "icons", "to_beginning.gif"))

        # заглушка/статус
        self.__dummy_var = tk.StringVar(self)
        self.dummy_label = tk.Label(self, textvariable=self.__dummy_var)

        # переключатель видов
        self.switch_frame = tk.Frame(self)
        self.switch_frame.grid_columnconfigure(2, weight=1)

        self.prev_button = nb.Button(self.switch_frame, image=self.IMG_PREV)
        self.prev_button_dark = tk.Label(self.switch_frame, image=self.IMG_PREV)
        theme.register_alternate(
            (self.prev_button, self.prev_button_dark, self.prev_button_dark),
            {"column": 0, "row": 0}
        )
        self.prev_button.bind('<Button-1>', self.__prev)
        theme.button_bind(self.prev_button_dark, self.__prev)

        self.to_beginning_button = nb.Button(self.switch_frame, image=self.IMG_TO_BEGINNING)
        self.to_beginning_button_dark = tk.Label(self.switch_frame, image=self.IMG_TO_BEGINNING)
        theme.register_alternate(
            (self.to_beginning_button, self.to_beginning_button_dark, self.to_beginning_button_dark),
            {"column": 1, "row": 0}
        )
        self.to_beginning_button.bind('<Button-1>', self.__on_to_beginning_button_clicked)
        theme.button_bind(self.to_beginning_button_dark, self.__on_to_beginning_button_clicked)

        self.__switch_text_var = tk.StringVar(self.switch_frame)
        self.switch_text_label = tk.Label(self.switch_frame, textvariable=self.__switch_text_var)
        self.switch_text_label.grid(column=2, row=0, padx=3)

        self.pin_button = nb.Button(self.switch_frame, image=self.IMG_PIN)
        self.pin_button_dark = tk.Label(self.switch_frame, image=self.IMG_PIN)
        theme.register_alternate(
            (self.pin_button, self.pin_button_dark, self.pin_button_dark),
            {"column": 3, "row": 0}
        )
        self.pin_button.bind('<Button-1>', self.__on_pin_button_clicked)
        theme.button_bind(self.pin_button_dark, self.__on_pin_button_clicked)

        self.next_button = nb.Button(self.switch_frame, image=self.IMG_NEXT)
        self.next_button_dark = tk.Label(self.switch_frame, image=self.IMG_NEXT)
        theme.register_alternate(
            (self.next_button, self.next_button_dark, self.next_button_dark),
            {"column": 4, "row": 0}
        )
        self.next_button.bind('<Button-1>', self.__next)
        theme.button_bind(self.next_button_dark, self.__next)

        # регион локации и количество планет с видом
        self.region_frame = tk.Frame(self)
        self.region_frame.grid_columnconfigure(0, weight=1)

        self.__region_var = tk.StringVar(self.region_frame)
        self.region_label = tk.Label(self.region_frame, textvariable=self.__region_var)

        self.__locations_count: int
        self.__locations_count_var = tk.StringVar(self.region_frame)
        self.locations_count_label = tk.Label(self.region_frame, textvariable=self.__locations_count_var)

        self.region_label.grid(column=0, row=0, sticky="W")
        self.locations_count_label.grid(column=1, row=0, sticky="E")

        # ближайшая локация и расстояние до неё
        self.closest_location_frame = tk.Frame(self)
        self.closest_location_frame.grid_columnconfigure(0, weight=1)

        self.__closest_location_var = tk.StringVar(self.closest_location_frame)
        self.closest_location_label = tk.Label(self.closest_location_frame, textvariable=self.__closest_location_var)

        self.__distance: float
        self.__distance_var = tk.StringVar(self.closest_location_frame)
        self.distance_label = tk.Label(self.closest_location_frame, textvariable=self.__distance_var)

        self.closest_location_label.grid(column=0, row=0, sticky="W")
        self.distance_label.grid(column=1, row=0, sticky="E")

        # кнопки удаления локации и копирования системы
        self.buttons_frame = tk.Frame(self)
        self.buttons_frame.grid_columnconfigure((0, 1), weight=1, uniform="equal")

        self.copy_button = nb.Button(self.buttons_frame, text="Копировать систему", padding=(10, 0))
        self.copy_button_dark = tk.Label(self.buttons_frame, text="Копировать систему", fg="white", padx=10)
        theme.register_alternate(
            (self.copy_button, self.copy_button_dark, self.copy_button_dark),
            {"column": 0, "row": 0, "sticky": "EW"}
        )
        self.copy_button.bind('<Button-1>', self.__copy)
        theme.button_bind(self.copy_button_dark, self.__copy)

        self.delete_button = nb.Button(self.buttons_frame, text="Я здесь уже был!", padding=(10, 0))
        self.delete_button_dark = tk.Label(self.buttons_frame, text="Я здесь уже был!", fg="white", padx=10)
        theme.register_alternate(
            (self.delete_button, self.delete_button_dark, self.delete_button_dark),
            {"column": 1, "row": 0, "sticky": "EW"}
        )
        self.delete_button.bind('<Button-1>', self.__delete)
        theme.button_bind(self.delete_button_dark, self.__delete)

        # кнопка фильтра по регионам
        self.filter_frame = tk.Frame(self)      # EDMC использует grid для пар виджетов, а мы хотим в self юзать pack
        self.filter_frame.grid_columnconfigure(0, weight=1)

        self.filter_button = nb.Button(self.filter_frame, text="Фильтр регионов")
        self.filter_button_dark = tk.Label(self.filter_frame, text="Фильтр регионов", fg="white")
        theme.register_alternate(
            (self.filter_button, self.filter_button_dark, self.filter_button_dark),
            {"column": 0, "row": 0, "sticky": "EW"}
        )
        self.filter_button.bind("<Button-1>", self.__create_filter_window)
        theme.button_bind(self.filter_button_dark, self.__create_filter_window)

        # упаковываем до данных по местоположению
        self.set_status("Местоположение неизвестно.\nТребуется прыжок или перезапуск игры.")
        BasicThread(name="BioPatrolDataReader", target=self.load_data).start()
        self.grid(column=0, row=gridrow, sticky="NWSE")


    def load_data(self):
        with self.__threadlock:
            while True:
                try:
                    self.after(0, lambda: None)
                except RuntimeError:        # tk isn't ready
                    sleep(1)
                else:
                    break

            self.body = None
            try:
                with open(Path(self.plugin_dir, "data", self.FILENAME_BIO), 'r') as f:
                    self.__bio_found = json.load(f)
            except Exception:
                self.set_status(f"Данные по находкам не найдены или повреждены (data/{self.FILENAME_BIO})")
                self.__bio_found = {}

                # {
                #   "signalCount": 5
                #   "signals" : [
                #     "Tussock Pennatis - Yellow",
                #     "Tussock Pennatis - Yellow",
                #     "Tussock Pennatis - Yellow",
                #     "Tussock Pennatis - Yellow",
                #     "Tussock Pennatis - Yellow"
                #   ]
                # }

            try:
                with open(Path(self.plugin_dir, "data", self.FILENAME_FLAT), 'r') as f:
                    raw_data = json.load(f)
                    raw_formed_at = datetime.fromisoformat(raw_data.get("timestamp", "1970-01-01"))
            except Exception:
                self.set_status(f"Данные по биологии не найдены или повреждены (data/{self.FILENAME_FLAT})")
                raw_data = {}

            try:
                with gzip.open(Path(self.plugin_dir, "data", self.FILENAME_RAW), 'r') as f:
                    archive_data = json.load(f)
                    archive_formed_at = datetime.fromisoformat(archive_data.get("timestamp", "1970-01-01"))
            except Exception:
                self.set_status(f"Данные по биологии не найдены или повреждены (data/{self.FILENAME_RAW})")
                archive_data = {}

            if not raw_data:
                if archive_data:
                    self.__raw_data = self.process_archive_data(archive_data)
                    self.save_data()
                else:
                    self._enabled = False
                    return
            else:
                if archive_data and (raw_formed_at < archive_formed_at):
                    self.__raw_data = self.process_archive_data(archive_data)
                    self.save_data()
                else:
                    self.__raw_data = raw_data

            for k, v in self.__bio_found.items():
                planet = k

                for species, data in self.__raw_data["bio"].items():
                    if planet not in data["locations"]:
                        continue

                    species_genus = species.split()[0]
                    known_genuses = self.__bio_found[planet].get("genuses", [])

                    if known_genuses is not None and species_genus not in known_genuses:
                        debug(f">> Removing {species} prediction for {planet} - genus has been ruled out by DSS")
                        del data["locations"][planet]

                for bioname in v["signals"]:
                    genus = bioname.split()[0]
                    self.process_genus_bio(genus, bioname, planet)

            self.set_status("Данные импортированы.\nТребуется прыжок или перезапуск игры.")
            self._enabled = True


    def process_archive_data(self, raw_data: dict):
        data = {
            "timestamp": raw_data.get("timestamp", "1970-01-01"),
            "bio": {}
        }

        for region, region_data in raw_data["bio"].items():
            for species, species_data in region_data.items():
                priority = species_data["priority"]
                if species not in data["bio"]:
                    data["bio"][species] = {"locations": {}}

                for location in species_data["locations"]:
                    location["region"] = region
                    location["priority"] = priority
                    body = location["body"]
                    del location["body"]
                    data["bio"][species]["locations"][body] = location

            self.set_status(f"Обработан регион {region}")

        return data


    def save_data(self):
        with open(Path(self.plugin_dir, "data", self.FILENAME_FLAT), 'w') as f:
            json.dump(self.__raw_data, f, ensure_ascii=False)

        with open(Path(self.plugin_dir, "data", self.FILENAME_BIO), 'w') as f:
            json.dump(self.__bio_found, f, ensure_ascii=False)


    def process_genus_bio(self, genus, bioname, planet, report=False, entry_region=None):
        region = None
        priority = 1
        if bioname in self.__raw_data["bio"]:
            locations = self.__raw_data["bio"][bioname]["locations"]
            if planet in locations:
                region = locations[planet]["region"]
                priority = locations[planet]["priority"]

        # We may know the region from CodexEntry event
        if entry_region is not None:
            region = entry_region

        if report is True:
            url_params = {
                "entry.1220081267": self.cmdr,
                "entry.82601913": region,
                "entry.1533043520": planet,
                "entry.1614339748": get_priority_text(priority),
                "entry.393624172": bioname
            }
            url = f'{URL_GOOGLE}/1FAIpQLSfp4rPNSOVf5V-LYLEUXCKomDBaHo92lPwfp9YJDrml2QGUQQ/formResponse?usp=pp_url&{"&".join([f"{k}={v}" for k, v in url_params.items()])}'  # noqa: E501
            Reporter(url).start()

        debug(f"Found {bioname} (genus: {genus}) at {planet} (priority: {priority})")
        for species, data in self.__raw_data["bio"].items():
            if planet not in data["locations"]:
                continue

            remove_planet = False
            # only one species per genus allowed
            species_genus = species.split()[0]
            if genus == species_genus and bioname != species:
                debug(f">> Removing {species} prediction for {planet} - matching genus has been found")
                remove_planet = True

            # found all signals, clear planet from lists
            signals_found = len(self.__bio_found[planet]["signals"])
            signals_count = self.__bio_found[planet]["signalCount"]
            if signals_found == signals_count:
                debug(f">> Removing {species} prediction for {planet} - all {signals_count} signals discovered")
                remove_planet = True

            if remove_planet is True:
                del data["locations"][planet]
                # don't update ui on EDMC startup
                if self._enabled:
                    self.update_pos()

            # new codex entry detected, remove all from region
            if priority > 1:
                data_locations_new = {}
                for k, v in data["locations"].items():
                    if species == bioname and v["region"] == region:
                        debug(f">> Removing {species} prediction for {k} - new codex entry in region {region}")
                        continue

                    data_locations_new[k] = v

                data["locations"] = data_locations_new

            # new galactic entry detected
            if priority > 2:
                for k, v in data["locations"].items():
                    if species == bioname:
                        if v["region"] != region:
                            debug(f'>> Changing {species} prediction for {k} - found in {region}, downgrading priority in {v["region"]}')   # noqa: E501
                            v["priority"] = 2
                            continue


    def biofound_init_body(self, body, signal_count=None):
        if body not in self.__bio_found:
            self.__bio_found[body] = {
                "signalCount": signal_count,
                "signals": [],
                "genuses": None
            }

    def biofound_set_genuses(self, body, genuses):
        self.__bio_found[body]["genuses"] = genuses

    def biofound_add_signal(self, body, signal):
        if signal not in self.__bio_found[body]["signals"]:
            self.__bio_found[body]["signals"].append(signal)


    def on_journal_entry(self, entry: JournalEntry):
        required_events = ["Location", "FSDJump", "ScanOrganic", "SAASignalsFound", "FSSBodySignals", "FSSAllBodiesFound", "CodexEntry"]    # noqa: E501
        event = entry.data["event"]
        if event not in required_events:
            return

        with self.__threadlock:
            if not self._enabled:       # на случай, если попытка чтения данных завершилась ошибкой
                return

            if event in ("Location", "FSDJump"):
                self.__update_data(entry)

            elif event == "ScanOrganic":
                genus = codex_to_english_genuses.get(entry.data["Genus"], entry.data["Genus"])
                bioname = codex_to_english_variants.get(entry.data["Variant"], entry.data["Variant"])
                self.set_status(f"Scanned {bioname} at {self.body}")

                self.biofound_add_signal(self.body, bioname)

                # update data
                self.process_genus_bio(genus, bioname, self.body, report=True)

                self.save_data()
                self.__update_data(entry)

            elif event == "CodexEntry":
                if entry.data["SubCategory"] != "$Codex_SubCategory_Organic_Structures;":
                    return

                if self.body is None:
                    return

                bioname = codex_to_english_variants.get(entry.data["Name"], entry.data["Name"])
                region = entry.data["Region_Localised"]

                # HACK -- CodexEntry does not have 'Genus' key
                genus = bioname.split()[0]
                self.set_status(f"Scanned {bioname} at {self.body}")

                self.biofound_add_signal(self.body, bioname)

                # update data
                self.process_genus_bio(genus, bioname, self.body, report=True, entry_region=region)

                self.save_data()
                self.__update_data(entry)

            elif event == "SAASignalsFound" and entry.data.get("Genuses"):
                genuses = [codex_to_english_genuses.get(i["Genus"], i["Genus"]) for i in entry.data["Genuses"]]
                bodyName = entry.data["BodyName"]

                self.biofound_init_body(bodyName, len(genuses))
                self.biofound_set_genuses(bodyName, genuses)

                for species, data in self.__raw_data["bio"].items():
                    if bodyName in data["locations"] and species.split()[0] not in genuses:
                        del data["locations"][bodyName]
                        self.__update_data(entry)
                        self.update_pos()
                self.save_data()

            elif event == "FSSBodySignals":
                name = entry.data["BodyName"]
                for signal in entry.data.get("Signals", []):
                    if signal["Type"] == "$SAA_SignalType_Biological;":
                        self.signals_in_system[name] = signal["Count"]
                        self.biofound_init_body(name, signal["Count"])

            elif event == "FSSAllBodiesFound":
                planets_to_remove = set()

                for species, species_data in self.__raw_data["bio"].items():
                    for planet, planet_data in species_data["locations"].items():
                        if planet_data["system"] != entry.data["SystemName"]:
                            continue

                        if planet not in self.signals_in_system:
                            if planet in self.__bio_found:
                                signalCount = self.__bio_found[planet].get("signalCount", 0)
                                if signalCount > 0:
                                    debug(f'>> Planet {planet} has been kept: has {signalCount} signals')
                                    continue

                            planets_to_remove.add(planet)

                for planet in planets_to_remove:
                    self.biofound_init_body(planet, 0)
                    for species, species_data in self.__raw_data["bio"].items():
                        if planet in species_data["locations"]:
                            del species_data["locations"][planet]

                self.__update_data(entry)
                self.update_pos()
                self.save_data()
                self.signals_in_system.clear()

    def on_dashboard_entry(self, cmdr, is_beta, entry):
        if self.cmdr != cmdr and cmdr is not None:
            self.cmdr = cmdr

        if "BodyName" not in entry:
            self.body = None
            return

        if self.body != entry["BodyName"]:
            self.body = entry["BodyName"]


    def set_status(self, text: str):
        def __inner(self):
            self.switch_frame.pack_forget()
            self.region_frame.pack_forget()
            self.closest_location_frame.pack_forget()
            self.buttons_frame.pack_forget()
            self.__dummy_var.set(text)
            self.dummy_label.pack(side="top", fill="x")
        self.after(0, __inner, self)


    def show(self):
        def __inner(self):
            self.dummy_label.pack_forget()
            self.switch_frame.pack(side="top", fill="x")
            self.region_frame.pack(side="top", fill="x")
            self.closest_location_frame.pack(side="top", fill="x")
            self.buttons_frame.pack(side="top", fill="x")
            self.filter_frame.pack(side="bottom", fill="x")
        self.after(0, __inner, self)


    @property
    def selected_bio(self) -> str:
        return self.__selected_bio

    @selected_bio.setter
    def selected_bio(self, value: str):
        self.__selected_bio = value
        self.__update_switch_text(bio_name=value, priority=self.priority)


    @property
    def priority(self):
        return self.__priority

    @priority.setter
    def priority(self, value: int):
        self.__priority = value
        self.__update_switch_text(priority=value, bio_name=self.selected_bio)


    def __update_switch_text(self, priority: int, bio_name: str):
        priority_text = get_priority_text(priority)
        if priority_text == "":
            self.__switch_text_var.set(bio_name)
        else:
            self.__switch_text_var.set(f"{priority_text}: {bio_name}")


    @property
    def region(self):
        return self.__region_var.get()

    @region.setter
    def region(self, value: str):
        self.__region_var.set(value)


    @property
    def count(self):
        return self.__locations_count

    @count.setter
    def count(self, value: int):
        self.__locations_count = value
        if value % 100 in (11, 12, 13, 14):
            text = "найденных локаций"
        elif value % 10 == 1:
            text = "найденная локация"
        elif value % 10 in (2, 3, 4):
            text = "найденные локации"
        else:
            text = "найденных локаций"
        self.__locations_count_var.set(f"{value} {text}")


    @property
    def closest_location(self) -> str:
        return self.__closest_location_var.get()

    @closest_location.setter
    def closest_location(self, value: str):
        self.__closest_location_var.set(value)


    @property
    def distance_to_closest(self) -> float:
        return self.__distance

    @distance_to_closest.setter
    def distance_to_closest(self, value: float):
        self.__distance = value
        self.__distance_var.set(f"{value:.2f} ly")


    def get_species_left_to_discover(self):
        return [bio_item for bio_item, data in self.__raw_data["bio"].items() if len(data["locations"]) > 0]


    def __update_data(self, entry: JournalEntry):
        current_coords = entry.coords
        if None in [entry.coords.x, entry.coords.y, entry.coords.z] and "StarPos" in entry.data:
            starpos = entry.data["StarPos"]
            current_coords = Coords(starpos[0], starpos[1], starpos[2])
        self.current_coords = current_coords
        self.__update_data_coords(self.current_coords)


    def __update_data_coords(self, current_coords: Coords):
        def get_closest(current_coords, locations: dict):
            closest = None
            min_dist = float("inf")
            found = 0
            for body, loc in locations.items():
                if loc["region"] not in self.enabled_regions:
                    continue
                found += 1
                loc_coords = Coords(loc["x"], loc["y"], loc["z"])
                if (loc_distance := distance_between(current_coords, loc_coords)) < min_dist:
                    min_dist = loc_distance
                    closest = (body, loc)

            if not found:
                return None

            body, location = closest
            coords = Coords(location["x"], location["y"], location["z"])
            distance = distance_between(current_coords, coords)
            system = location["system"]
            priority = location["priority"]
            region = location["region"]
            return system, body, distance, coords, priority, region, found

        self.set_status("Пересчёт данных...")
        data = []
        for bio_item in self.get_species_left_to_discover():
            closest_location = get_closest(current_coords, self.__raw_data["bio"][bio_item]["locations"])
            if closest_location is None:
                continue
            system, body, distance, coords, priority, region, count = closest_location
            data.append({
                "species": bio_item,
                "priority": priority,
                "closest_location": body,
                "_system": system,
                "coords": coords,
                "distance": distance,
                "region": region,
                "count": count
            })
        data.sort(key=lambda x: (-x["priority"], x["distance"]))
        self.data = data
        self.update_pos()


    def update_pos(self):
        if len(self.data) == 0:
            self.set_status("Либо все виды найдены, либо что-то сломалось.")
            return
        if not self.pinned_bio:
            self.pos = 0
        else:
            for i, bio in enumerate(self.data):
                if bio["species"] == self.pinned_bio:
                    self.pos = i
                    break
            else:
                self.pinned_bio = None
                self.pos = 0
        self.show()

    @property
    def pos(self):
        return self.__pos

    @pos.setter
    def pos(self, value: int):
        # вынужденная обёртка ради потокобезопасности
        self.after(0, self.__set_pos, value)

    def __set_pos(self, value: int):
        bio_item = self.data[value]
        self.selected_bio = bio_item["species"]
        self.priority = bio_item["priority"]
        self.count = bio_item["count"]
        self.closest_location = bio_item["closest_location"]
        self.distance_to_closest = bio_item["distance"]
        self.region = bio_item["region"]
        self.__pos = value
        self.__update_buttons_configuration()


    def __on_pin_button_clicked(self, event):
        if self.pinned_bio == self.selected_bio:
            self.pinned_bio = None
        else:
            self.pinned_bio = self.selected_bio
        self.__update_buttons_configuration()


    def __on_to_beginning_button_clicked(self, event):
        self.pos = 0


    def __prev(self, event):
        if str(event.widget["state"]) == tk.DISABLED:
            return
        self.pos -= 1


    def __next(self, event):
        if str(event.widget["state"]) == tk.DISABLED:
            return
        self.pos += 1


    def __update_buttons_configuration(self):
        self.prev_button.configure(state="disabled" if self.pos == 0 else "normal")
        self.prev_button_dark.configure(state="disabled" if self.pos == 0 else "normal")
        self.next_button.configure(state="disabled" if (self.pos == len(self.data) - 1) else "normal")
        self.next_button_dark.configure(state="disabled" if (self.pos == len(self.data) - 1) else "normal")
        if self.pinned_bio == self.selected_bio:
            self.pin_button.configure(image=self.IMG_PINNED)
            self.pin_button_dark.configure(image=self.IMG_PINNED)
        else:
            self.pin_button.configure(image=self.IMG_PIN)
            self.pin_button_dark.configure(image=self.IMG_PIN)


    def __copy(self, event):
        copyclip(self.data[self.pos]["_system"])


    def __delete(self, event):
        planet = self.data[self.pos]["closest_location"]
        coords = self.data[self.pos]["coords"]

        if planet not in self.__bio_found:
            self.set_status(f"Сначала просканируйте {planet} с помощью DSS.")
            self.after(3000, self.show)
            return

        for species, data in self.__raw_data["bio"].items():
            if planet in data["locations"]:
                del data["locations"][planet]
                self.biofound_init_body(planet, 0)

                self.__update_data_coords(coords)
                self.save_data()

                self.set_status(f"Планета {planet} удалена из списка.")
                self.after(3000, self.show)


    def __create_filter_window(self, event):
        if self.__region_filter_window is not None:
            return
        self.__region_filter_window = RegionFilterWindow(self, self.__region_filter_callback)
        self.__region_filter_window.protocol("WM_DELETE_WINDOW", self.__region_filter_closed)


    def __region_filter_closed(self):
        self.__region_filter_window.destroy()
        self.__region_filter_window = None


    def __region_filter_callback(self, config: dict[str, bool]):
        self.enabled_regions = [region for region, enabled in config.items() if enabled]
        self.__region_filter_window.destroy()
        self.__region_filter_window = None
        self.__update_data_coords(self.current_coords)
