import gzip
import json
import tkinter as tk
from datetime import datetime
from pathlib import Path
from math import sqrt

from modules.lib.module import Module
from modules.lib.journal import JournalEntry, Coords
from modules.patrol.patrol_module import copyclip
from modules.lib.context import global_context

import myNotebook as nb
from theme import theme
from modules.bio_dicts import codex_to_english_variants, codex_to_english_genuses


def distance_between(a: Coords, b: Coords):
    dx = a.x - b.x
    dy = a.y - b.y
    dz = a.z - b.z
    return sqrt(dx**2 + dy**2 + dz**2)


class BioPatrol(tk.Frame, Module):
    FILENAME_RAW = 'bio.json.gz'
    FILENAME_FLAT = 'bio-flat.json'
    FILENAME_BIO = 'bio-found.json'

    def __init__(self, parent, gridrow):
        super().__init__(parent)

        self.plugin_dir = global_context.plugin_dir
        self.data: list[dict] = []
        self._enabled = True
        self.__pos = 0
        self.__priority = 0
        self.__selected_bio = ""

        self.IMG_PREV = tk.PhotoImage(
            file=Path(self.plugin_dir, "icons", "left_arrow.gif")
        )
        self.IMG_NEXT = tk.PhotoImage(
            file=Path(self.plugin_dir, "icons", "right_arrow.gif")
        )

        # заглушка/статус
        self.__dummy_var = tk.StringVar(self)
        self.dummy_label = tk.Label(self, textvariable=self.__dummy_var)

        # переключатель видов
        self.switch_frame = tk.Frame(self)
        self.switch_frame.grid_columnconfigure(1, weight=1)

        self.prev_button = nb.Button(self.switch_frame, image=self.IMG_PREV)
        self.prev_button_dark = tk.Label(self.switch_frame, image=self.IMG_PREV)
        theme.register_alternate(
            (self.prev_button, self.prev_button_dark, self.prev_button_dark),
            {"column": 0, "row": 0}
        )
        self.prev_button.bind('<Button-1>', self.__prev)
        theme.button_bind(self.prev_button_dark, self.__prev)

        self.next_button = nb.Button(self.switch_frame, image=self.IMG_NEXT)
        self.next_button_dark = tk.Label(self.switch_frame, image=self.IMG_NEXT)
        theme.register_alternate(
            (self.next_button, self.next_button_dark, self.next_button_dark),
            {"column": 2, "row": 0}
        )
        self.next_button.bind('<Button-1>', self.__next)
        theme.button_bind(self.next_button_dark, self.__next)

        self.__switch_text_var = tk.StringVar(self.switch_frame)
        self.switch_text_label = tk.Label(self.switch_frame, textvariable=self.__switch_text_var)
        self.switch_text_label.grid(column=1, row=0, padx=3)

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
        self.buttons_frame.grid_columnconfigure(0, weight=1)
        self.buttons_frame.grid_columnconfigure(1, weight=1)

        self.copy_button = nb.Button(self.buttons_frame, text="Копировать систему")
        self.copy_button_dark = tk.Label(self.buttons_frame, text="Копировать систему", fg="white")
        theme.register_alternate(
            (self.copy_button, self.copy_button_dark, self.copy_button_dark),
            {"column": 0, "row": 0, "sticky": "EW"}
        )
        self.copy_button.bind('<Button-1>', self.__copy)
        theme.button_bind(self.copy_button_dark, self.__copy)

        self.delete_button = nb.Button(self.buttons_frame, text="На планете нет биосигналов!")
        self.delete_button_dark = tk.Label(self.buttons_frame, text="На планете нет биосигналов!", fg="white")
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
        self.set_status("Местоположение неизвестно. Требуется прыжок или перезапуск игры.")
        self.load_data()
        self.grid(column=0, row=gridrow, sticky="NWSE")


    def load_data(self):
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

        self.set_status("Данные импортированы. Требуется прыжок или перезапуск игры.")

        for k, v in self.__bio_found.items():
            planet = k
            for bioname in v["signals"]:
                genus = bioname.split()[0]
                self.process_genus_bio(genus, bioname, planet)


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


    def process_genus_bio(self, genus, bioname, planet):
        region = None
        priority = 1
        if bioname in self.__raw_data["bio"]:
            locations = self.__raw_data["bio"][bioname]["locations"]
            if planet in locations:
                region = locations[planet]["region"]
                priority = locations[planet]["priority"]

        for species, data in self.__raw_data["bio"].items():
            if planet in data["locations"]:
                # remove all "region new" from the list
                removed = {k: v for k, v in data["locations"].items() if v["region"] == region}
                for k, v in removed.items():
                    print(f"Found {bioname} at region {region}, removing {k} from the list")

                data["locations"] = {k: v for k, v in data["locations"].items() if v["region"] != region}

                for body, body_data in data["locations"].items():
                    # This is not a galactic new anymore, mark as region new
                    if priority == 3:
                        body_data["priority"] == 2

                # everything has been found, clean up
                if planet in data["locations"]:
                    if len(self.__bio_found[planet]["signals"]) == self.__bio_found[planet]["signalCount"]:
                        del data["locations"][planet]
                    else:
                        # something left to find, clear all with matching genus
                        species_genus = species.split()[0]

                        if genus == species_genus and bioname != species:
                            del data["locations"][planet]


    def on_journal_entry(self, entry: JournalEntry):
        if not self._enabled:
            return

        event = entry.data["event"]

        if event in ("Location", "FSDJump"):
            self.__update_data(entry)
            self.pos = next((i for i, bio in enumerate(self.data) if bio["species"] == self.selected_bio), 0)
            self.after(0, self.show)

        elif event == "ScanOrganic":
            genus = codex_to_english_genuses.get(entry.data["Genus"], entry.data["Genus"])
            bioname = codex_to_english_variants.get(entry.data["Variant"], entry.data["Variant"])
            self.set_status(f"Scanned {bioname} at {self.body}")

            if bioname not in self.__bio_found[self.body]["signals"]:
                self.__bio_found[self.body]["signals"].append(bioname)

            # update data
            self.process_genus_bio(genus, bioname, self.body)

            self.save_data()
            self.__update_data(entry)
            self.pos = next((i for i, bio in enumerate(self.data) if bio["species"] == self.selected_bio), 0)
            self.after(0, self.show)

        elif event == "SAASignalsFound" and entry.data.get("Genuses"):
            genuses = [codex_to_english_genuses.get(i["Genus"], i["Genus"]) for i in entry.data["Genuses"]]
            bodyName = entry.data["BodyName"]

            if bodyName not in self.__bio_found:
                self.__bio_found[bodyName] = {
                    "signalCount": len(genuses),
                    "signals": []
                }

            for species, data in self.__raw_data["bio"].items():
                if bodyName in data["locations"] and species.split()[0] not in genuses:
                    del data["locations"][bodyName]
                    self.__update_data(entry)
                    self.pos = next((i for i, bio in enumerate(self.data) if bio["species"] == self.selected_bio), 0)
                    self.after(0, self.show)
            self.save_data()


    def on_dashboard_entry(self, cmdr, is_beta, entry):
        if "BodyName" not in entry:
            self.body = None
            return

        if self.body != entry["BodyName"]:
            self.body = entry["BodyName"]


    def set_status(self, text: str):
        self.switch_frame.pack_forget()
        self.region_frame.pack_forget()
        self.closest_location_frame.pack_forget()
        self.buttons_frame.pack_forget()
        self.__dummy_var.set(text)
        self.dummy_label.pack(side="top", fill="x")


    def show(self):
        self.dummy_label.pack_forget()
        self.switch_frame.pack(side="top", fill="x")
        self.region_frame.pack(side="top", fill="x")
        self.closest_location_frame.pack(side="top", fill="x")
        self.buttons_frame.pack(side="top", fill="x")
        self.filter_frame.pack(side="bottom", fill="x")


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
        match priority:
            case 3: priority_text = "ПЕРВЫЙ В ГАЛАКТИКЕ! "
            case 2: priority_text = "Открытие региона: "
            case _: priority_text = ""
        self.__switch_text_var.set(priority_text + bio_name)


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

        self.__update_data_coords(current_coords)


    def __update_data_coords(self, coords: Coords):
        def get_closest(current_coords, locations):
            closest_key = min(locations, key=lambda l: distance_between(current_coords, Coords(locations[l]["x"], locations[l]["y"], locations[l]["z"])))
            closest = locations[closest_key]
            _coords = Coords(closest["x"], closest["y"], closest["z"])
            _distance = distance_between(current_coords, _coords)
            _system = closest["system"]
            _body = closest_key
            _priority = closest["priority"]
            _region = closest["region"]
            return _system, _body, _distance, _coords, _priority, _region

        data = []
        for bio_item in self.get_species_left_to_discover():
            closest_system, closest_body, distance, closest_coords, priority, region = get_closest(coords, self.__raw_data["bio"][bio_item]["locations"])
            data.append({
                "species": bio_item,
                "priority": priority,
                "closest_location": closest_body,
                "_system": closest_system,
                "coords": closest_coords,
                "distance": distance,
                "region": region,
                "count": len(self.__raw_data["bio"][bio_item]["locations"])
            })
        data.sort(key=lambda x: (-x["priority"], x["distance"]))
        self.data = data
        if len(self.data) == 0:
            self.set_status("Либо все виды найдены, либо что-то сломалось.")
            self._enabled = False


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


    def __prev(self, event):
        self.pos -= 1


    def __next(self, event):
        self.pos += 1


    def __update_buttons_configuration(self):
        self.prev_button.configure(state="disabled" if self.pos == 0 else "normal")
        self.next_button.configure(state="disabled" if (self.pos == len(self.data) - 1) else "normal")


    def __copy(self, event):
        copyclip(self.data[self.pos]["_system"])


    def __delete(self, event):
        planet = self.data[self.pos]["closest_location"]
        coords = self.data[self.pos]["coords"]

        if planet in self.__bio_found and self.__bio_found[planet].get("signalCount", 0) != 0:
            return

        for species, data in self.__raw_data["bio"].items():
            if planet in data["locations"]:
                del data["locations"][planet]
                self.__update_data_coords(coords)
                self.pos = next((i for i, bio in enumerate(self.data) if bio["species"] == self.selected_bio), 0)
                self.after(0, self.show)
                self.save_data()


    def __create_filter_window(self):
        pass
