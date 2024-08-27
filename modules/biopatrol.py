import json
import tkinter as tk
from pathlib import Path
from math import sqrt

from modules.lib.module import Module
from modules.lib.journal import JournalEntry, Coords
from modules.patrol.patrol_module import copyclip
from modules.lib.context import global_context

from tkinter import ttk as nb


def distance_between(a: Coords, b: Coords):
    dx = a.x - b.x
    dy = a.y - b.y
    dz = a.z - b.z
    return sqrt(dx**2 + dy**2 + dz**2)


class BioPatrol(nb.Frame, Module):
    FILENAME = 'bio.json'

    def __init__(self, parent, gridrow):
        super().__init__(parent)

        self.plugin_dir = global_context.plugin_dir
        self.data: list[dict] = []
        self._enabled = True
        self.__pos = 0

        self.IMG_PREV = tk.PhotoImage(
            file=Path(self.plugin_dir, "icons", "left_arrow.gif")
        )
        self.IMG_NEXT = tk.PhotoImage(
            file=Path(self.plugin_dir, "icons", "right_arrow.gif")
        )

        # заглушка/статус
        self.__dummy_var = tk.StringVar(self)
        self.dummy_label = nb.Label(self, textvariable=self.__dummy_var)

        # верхняя строка (переключатель)
        self.topframe = nb.Frame(self)
        self.topframe.grid_columnconfigure(1, weight=1)
        self.__selected_bio_var = tk.StringVar(self.topframe)

        self.prev_button = nb.Button(self.topframe, image=self.IMG_PREV, command=self.__prev)
        self.next_button = nb.Button(self.topframe, image=self.IMG_NEXT, command=self.__next)
        self.selected_bio_label = nb.Label(self.topframe, textvariable=self.__selected_bio_var)

        self.prev_button.grid(column=0, row=0)
        self.selected_bio_label.grid(column=1, row=0, padx=3)
        self.next_button.grid(column=2, row=0)

        # средняя строка (приоритет)
        self.__priority: int
        self.priority_var = tk.StringVar(self)
        self.priority_label = nb.Label(self, textvariable=self.priority_var)

        # нижняя строка (ближайшее местоположение)
        self.bottomframe = nb.Frame(self)
        self.bottomframe.grid_columnconfigure(1, weight=1)
        self.__distance: float
        self.__distance_var = tk.StringVar(self.bottomframe)
        self.__closest_location_var = tk.StringVar(self.bottomframe)

        self.distance_label = nb.Label(self.bottomframe, textvariable=self.__distance_var)
        self.closest_location_label = nb.Label(self.bottomframe, textvariable=self.__closest_location_var)
        self.copy_button = nb.Button(self.bottomframe, text="Copy system", command=self.__copy)

        self.distance_label.grid(column=0, row=0)
        self.closest_location_label.grid(column=1, row=0, padx=3)
        self.copy_button.grid(column=2, row=0)

        # упаковываем до данных по местоположению
        self.set_status("Ожидание ивента Location/FSDJump...")
        self.load_data()
        self.grid(column=0, row=gridrow, sticky="NWSE")


    def load_data(self):
        try:
            f = open(Path(self.plugin_dir, "data", self.FILENAME), 'r')
            self.__raw_data = json.load(f)
        except:
            self.set_status(f"Данные по биологии не найдены или повреждены (data/{self.FILENAME})")
            self._enabled = False


    def save_data(self):
        with open(Path(self.plugin_dir, "data", self.FILENAME), 'w') as f:
            json.dump(self.__raw_data, f, ensure_ascii=False, indent=2)
        
    
    def on_journal_entry(self, entry: JournalEntry):
        if not self._enabled:
            return
        
        event = entry.data["event"]

        if event in ("Location", "FSDJump"):
            self.__update_data(entry)
            self.pos = next((i for i, bio in enumerate(self.data) if bio["species"] == self.selected_bio), 0)
            self.after(0, self.show)

        elif event == "ScanOrganic" and entry.data["Variant_Localised"] in self.get_species_left_to_discover():
            bioname = entry.data["Variant_Localised"]
            self.__raw_data[bioname]["collected"] = True
            self.save_data()
            self.__update_data(entry)
            self.pos = 0

        elif event == "SAASignalsFound" and entry.data.get("Genuses") is not None:
            genuses = [i["Genus_Localised"] for i in entry.data["Genuses"]]
            for species, data in self.__raw_data.items():
                for location in data["locations"]:
                    if entry.data["BodyName"] == location["body"] and species.split()[0] not in genuses:
                        data["locations"].remove(location)
                        self.__update_data(entry)
                        self.pos = next((i for i, bio in enumerate(self.data) if bio["species"] == self.selected_bio), 0)
            self.save_data()
    

    def set_status(self, text: str):
        self.topframe.pack_forget()
        self.priority_label.pack_forget()
        self.bottomframe.pack_forget()
        self.__dummy_var.set(text)
        self.dummy_label.pack(side="top", fill="x")

    
    def show(self):
        self.dummy_label.pack_forget()
        self.topframe.pack(side="top", fill="x")
        self.priority_label.pack(side="top", fill="x")
        self.bottomframe.pack(side="top", fill="x")

    
    @property
    def selected_bio(self) -> str:
        return self.__selected_bio_var.get()
    
    @selected_bio.setter
    def selected_bio(self, value: str):
        self.__selected_bio_var.set(value)

    
    @property
    def priority(self):
        return self.__priority
    
    @priority.setter
    def priority(self, value: int):
        self.__priority = value
        match value:
            case 3: self.priority_var.set("GALACTIC NEW!")
            case 2: self.priority_var.set("Region new!")
            case _: self.priority_var.set("")


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
        return [bio_item for bio_item, data in self.__raw_data.items() if data["collected"] == False and len(data["locations"]) > 0]
    

    def __update_data(self, entry: JournalEntry):
        def get_closest(current_coords, locations):
            closest = min(locations, key=lambda l: distance_between(current_coords, Coords(l["x"], l["y"], l["z"])))
            coords = Coords(closest["x"], closest["y"], closest["z"])
            distance = distance_between(current_coords, coords)
            system = closest["system"]
            body = closest["body"]
            return system, body, distance, coords

        data = []
        current_coords = entry.coords
        for bio_item in self.get_species_left_to_discover():
            priority  = self.__raw_data[bio_item]["priority"]
            closest_system, closest_body, distance, coords = get_closest(current_coords, self.__raw_data[bio_item]["locations"])
            data.append({
                "species": bio_item,
                "priority": priority,
                "closest_location": closest_body,
                "_system": closest_system,
                "coords": coords,
                "distance": distance
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
        self.closest_location = bio_item["closest_location"]
        self.distance_to_closest = bio_item["distance"]
        self.__pos = value
        self.__update_buttons_configuration()


    def __prev(self):
        self.pos -= 1
        

    def __next(self):
        self.pos += 1


    def __update_buttons_configuration(self):
        self.prev_button.configure(state="normal")
        self.next_button.configure(state="normal")
        if self.pos == 0:
            self.prev_button.configure(state="disabled")
        if self.pos == len(self.data)-1:
            self.next_button.configure(state="disabled")
        

    def __copy(self):
        copyclip(self.data[self.pos]["_system"])