import os
import re
import gzip
import json
import sqlite3
import tkinter as tk
from tkinter import ttk
from tkinter.messagebox import askyesno, WARNING
from datetime import datetime
from pathlib import Path
from math import sqrt
from threading import Lock
from time import sleep
from typing import Callable, Any
from PIL import Image
from enum import Enum

from .debug import debug
from modules.lib.module import Module
from modules.lib.journal import JournalEntry, Coords
from modules.patrol.patrol_module import copyclip
from modules.lib.context import global_context
from modules.lib.thread import BasicThread
from modules.lib.conf import base_config as _edmc_config, config as plugin_config

import myNotebook as nb     # type: ignore
from theme import theme     # type: ignore
from modules.bio_dicts import codex_to_english_variants, codex_to_english_genuses, codex_to_english_regions, regions
from modules.sectors import split_ids, get_procgen_name, get_sector, get_boxel, get_children

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


class YobaStatus(Enum):
    IDLE = 0
    CALIBRATING = 1
    RUNNING = 2


class YobaWindow(tk.Toplevel):
    def __init__(self, parent: tk.Misc, callback: Callable[[dict[str, bool]], Any], system_data):
        super().__init__(parent)
        self.callback = callback

        self.frame = tk.Frame(self)

        self.title_label = tk.Label(self.frame, text="Your Outstanding Boxel Analyzer")
        self.title_label.grid(row=0, column=0, sticky="NWSE")

        system_name = system_data["name"]
        system_pgname = system_data["pgname"]

        sector_id, masscode_id, boxel_id, system_id, _ = split_ids(system_data["id64"])
        system_pgname = get_procgen_name(sector_id, masscode_id, boxel_id, system_id)
        system_boxel = f'{get_sector(sector_id)} {get_boxel(masscode_id, boxel_id)}'

        text = f"Текущая система: {system_name}\n"
        if system_name != system_pgname:
            text += f"Procgen имя: {system_pgname}\n"
        text += f"Текущий боксель: {system_boxel}"
        self.label = tk.Label(self.frame, text=text)
        self.label.grid(row=2, column=0, sticky="NWSE")

        self.depth_frame = tk.Frame(self.frame)

        self.yoba_depth = tk.IntVar(value=0)
        self.yoba_exclude_known = tk.IntVar(value=0)
        self.depth_header = tk.Label(self.depth_frame, text="Исследование вложенных бокселей:")
        self.depth0 = tk.Radiobutton(self.depth_frame, text="Только текущий боксель", value=0, variable=self.yoba_depth)
        self.depth1 = tk.Radiobutton(self.depth_frame, text="Глубина 1 боксель", value=1, variable=self.yoba_depth)
        self.depth2 = tk.Radiobutton(self.depth_frame, text="Глубина 2 бокселя", value=2, variable=self.yoba_depth)
        self.depth3 = tk.Radiobutton(self.depth_frame, text="Глубина 3 бокселя", value=3, variable=self.yoba_depth)
        self.exclude_known = tk.Checkbutton(self.depth_frame, text="Исключить исследованные системы", variable=self.yoba_exclude_known)

        if masscode_id < 3:
            self.depth3.configure(state="disabled")
        if masscode_id < 2:
            self.depth2.configure(state="disabled")
        if masscode_id < 1:
            self.depth1.configure(state="disabled")

        self.depth_header.pack()
        self.depth0.pack()
        self.depth1.pack()
        self.depth2.pack()
        self.depth3.pack()

        self.depth_frame.grid(row=3, column=0, sticky="NWSE")

        self.save_button = nb.Button(self.frame, text="Сохранить")
        self.save_button.bind("<Button-1>", self.__save)
        self.save_button.grid(row=4, column=0, sticky="NWSE")

        self.frame.pack()

    def __save(self, event):
        obj = {
            "depth" : self.yoba_depth.get(),
            "exclude_known" : self.yoba_exclude_known.get()
        }
        self.callback(obj)


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
        try:
            config = json.loads(plugin_config.get_str(cls.CONFIG_KEY))
        except json.JSONDecodeError:
            debug("Biopatrol regions filter config not found or invalid, resetting to default.")
            config = {r: True for r in regions}
            plugin_config.set(cls.CONFIG_KEY, json.dumps(config, ensure_ascii=False))
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


class BioPatrolJournalEntry:
    def __init__(
        self,
        system:     str,
        data:       dict,
        coords:     Coords,
        body:       str,
    ):
        self.system = system
        self.data = data
        self.coords = coords


class BioPatrol(tk.Frame, Module):
    FILENAME_RAW = 'bio.json.gz'
    FILENAME_FLAT = 'bio-flat.json'
    FILENAME_BIO = 'bio-found.json'
    FILENAME_BOXELS = 'boxels.json'

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
        # dict: (id64, bodyId) -> bodyName
        self.signals_in_system = {}
        self.__live_data = False
        self.__yoba_window: YobaWindow = None
        self.cmdr_id = None
        self.current_system_id64 = None
        self.yoba_current_boxel = None
        self.__yoba_calibrating = False
        self.__yoba_boxels = None

        # this is needed to stop the processing of old logs upon reaching fresh data
        self.last_processed_timestamp: datetime = None
        self.biopatrol = tk.Frame(self)

        self.IMG_PREV = tk.PhotoImage(file=Path(self.plugin_dir, "icons", "left_arrow.gif"))
        self.IMG_NEXT = tk.PhotoImage(file=Path(self.plugin_dir, "icons", "right_arrow.gif"))
        self.IMG_PIN = tk.PhotoImage(file=Path(self.plugin_dir, "icons", "pin.gif"))
        self.IMG_PINNED = tk.PhotoImage(file=Path(self.plugin_dir, "icons", "pinned.gif"))
        self.IMG_TO_BEGINNING = tk.PhotoImage(file=Path(self.plugin_dir, "icons", "to_beginning.gif"))
        self.IMG_BRABFUN = tk.PhotoImage(file=Path(self.plugin_dir, "icons", "brabfun.png"))
        self.IMG_YOBA_START = tk.PhotoImage(file=Path(self.plugin_dir, "icons", "51de0d93.png"))
        self.IMG_YOBA_STOP = tk.PhotoImage(file=Path(self.plugin_dir, "icons", "40845c5c.png"))

        self.grid_columnconfigure(0, weight=1)

        # заглушка/статус
        self.__dummy_var = tk.StringVar(self)
        self.dummy_label = tk.Label(self.biopatrol, textvariable=self.__dummy_var)

        # переключатель видов
        self.switch_frame = tk.Frame(self.biopatrol)
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
        self.region_frame = tk.Frame(self.biopatrol)
        self.region_frame.grid_columnconfigure(0, weight=1)

        self.__region_var = tk.StringVar(self.region_frame)
        self.region_label = tk.Label(self.region_frame, textvariable=self.__region_var)

        self.__locations_count: int
        self.__locations_count_var = tk.StringVar(self.region_frame)
        self.locations_count_label = tk.Label(self.region_frame, textvariable=self.__locations_count_var)

        self.region_label.grid(column=0, row=0, sticky="W")
        self.locations_count_label.grid(column=1, row=0, sticky="E")

        # ближайшая локация и расстояние до неё
        self.closest_location_frame = tk.Frame(self.biopatrol)
        self.closest_location_frame.grid_columnconfigure(0, weight=1)

        self.__closest_location_var = tk.StringVar(self.closest_location_frame)
        self.closest_location_label = tk.Label(self.closest_location_frame, textvariable=self.__closest_location_var)

        self.__distance: float
        self.__distance_var = tk.StringVar(self.closest_location_frame)
        self.distance_label = tk.Label(self.closest_location_frame, textvariable=self.__distance_var)

        self.closest_location_label.grid(column=0, row=0, sticky="W")
        self.distance_label.grid(column=1, row=0, sticky="E")

        # кнопки удаления локации и копирования системы
        self.buttons_frame = tk.Frame(self.biopatrol)
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
        # кнопка обработки старых логов
        self.filter_frame = tk.Frame(self.biopatrol)
        self.filter_frame.grid_columnconfigure(0, weight=1)

        self.filter_button = nb.Button(self.filter_frame, text="Фильтр регионов")
        self.filter_button_dark = tk.Label(self.filter_frame, text="Фильтр регионов", fg="white")
        theme.register_alternate(
            (self.filter_button, self.filter_button_dark, self.filter_button_dark),
            {"column": 0, "row": 0, "sticky": "EW"}
        )
        self.filter_button.bind("<Button-1>", self.__create_filter_window)
        theme.button_bind(self.filter_button_dark, self.__create_filter_window)

        self.old_logs_button = nb.Button(self.filter_frame, image=self.IMG_BRABFUN)
        self.old_logs_button_dark = tk.Label(self.filter_frame, image=self.IMG_BRABFUN, fg="white")
        theme.register_alternate(
            (self.old_logs_button, self.old_logs_button_dark, self.old_logs_button_dark),
            {"column": 1, "row": 0, "sticky": "EW"}
        )
        # for some obscure reason, using '<Button-1>' here makes the button get stuck in the pressed state
        self.old_logs_button.bind('<ButtonRelease-1>', self.__on_old_logs_processing_requested)
        theme.button_bind(self.old_logs_button_dark, self.__on_old_logs_processing_requested)

        # задаём и сохраняем порядок отображения виджетов
        self.dummy_label.grid(row=0, sticky="NWSE")
        self.switch_frame.grid(row=1, sticky="NWSE")
        self.region_frame.grid(row=2, sticky="NWSE")
        self.closest_location_frame.grid(row=3, sticky="NWSE")
        self.buttons_frame.grid(row=4, sticky="NWSE")
        self.filter_frame.grid(row=5, sticky="NWSE")
        self.dummy_label.grid_remove()
        self.switch_frame.grid_remove()
        self.region_frame.grid_remove()
        self.closest_location_frame.grid_remove()
        self.buttons_frame.grid_remove()
        self.filter_frame.grid_remove()

        # Boxel Explorer
        self.yoba = tk.Frame(self)

        self.yoba_start_frame = tk.Frame(self.yoba)
        self.yoba_start_frame.grid_columnconfigure(0, weight=1)
        self.__yoba_start_var = tk.StringVar(self.yoba_start_frame)
        self.yoba_start_label = tk.Label(self.yoba_start_frame, textvariable=self.__yoba_start_var)
        self.yoba_start_label.grid(column=0, row=0, sticky="W")

        self.yoba_start_button = nb.Button(self.yoba_start_frame, image=self.IMG_YOBA_START)
        self.yoba_start_button_dark = tk.Label(self.yoba_start_frame, image=self.IMG_YOBA_START)
        theme.register_alternate(
            (self.yoba_start_button, self.yoba_start_button_dark, self.yoba_start_button_dark),
            {"column": 1, "row": 0, "sticky": "EW"}
        )

        self.yoba_start_button.bind('<Button-1>', self.__yoba_window_create)
        theme.button_bind(self.yoba_start_button_dark, self.__yoba_window_create)

        self.yoba_stop_frame = tk.Frame(self.yoba)
        self.yoba_stop_frame.grid_columnconfigure(0, weight=1)
        self.__yoba_stop_var = tk.StringVar(self.yoba_stop_frame)
        self.yoba_stop_label = tk.Label(self.yoba_stop_frame, textvariable=self.__yoba_stop_var)
        self.yoba_stop_label.grid(column=0, row=0, sticky="W")

        self.yoba_stop_button = nb.Button(self.yoba_stop_frame, image=self.IMG_YOBA_STOP)
        self.yoba_stop_button_dark = tk.Label(self.yoba_stop_frame, image=self.IMG_YOBA_STOP)
        theme.register_alternate(
            (self.yoba_stop_button, self.yoba_stop_button_dark, self.yoba_stop_button_dark),
            {"column": 1, "row": 0, "sticky": "EW"}
        )

        self.yoba_stop_button.bind('<Button-1>', self.__yoba_abort)
        theme.button_bind(self.yoba_stop_button_dark, self.__yoba_abort)

        self.yoba_calibrate_frame = tk.Frame(self.yoba)
        self.yoba_calibrate_frame.grid_columnconfigure(0, weight=1)
        self.__yoba_calibrate_var = tk.StringVar(self.yoba_calibrate_frame)
        self.yoba_calibrate_label = tk.Label(self.yoba_calibrate_frame, textvariable=self.__yoba_calibrate_var)
        self.yoba_calibrate_label.grid(column=0, row=0, sticky="W")

        self.yoba_calibrate_button = nb.Button(self.yoba_calibrate_frame, text="Калибровка")
        self.yoba_calibrate_button_dark = tk.Label(self.yoba_calibrate_frame, text="Калибровка", fg="white")
        theme.register_alternate(
            (self.yoba_calibrate_button, self.yoba_calibrate_button_dark, self.yoba_calibrate_button_dark),
            {"column": 1, "row": 0, "sticky": "EW"}
        )
        self.yoba_calibrate_button.bind('<Button-1>', self.__yoba_calibrate)
        theme.button_bind(self.yoba_calibrate_button_dark, self.__yoba_calibrate)

        self.yoba_boxel_frame = tk.Frame(self.yoba)
        self.yoba_boxel_frame.grid_columnconfigure(0, weight=1)
        self.__yoba_boxel_var = tk.StringVar(self.yoba_boxel_frame)
        self.yoba_boxel_label = tk.Label(self.yoba_boxel_frame, textvariable=self.__yoba_boxel_var)
        self.yoba_boxel_label.grid(column=0, row=0, sticky="W")

        self.yoba_boxel_button = nb.Button(self.yoba_boxel_frame, text="Пропустить боксель")
        self.yoba_boxel_button_dark = tk.Label(self.yoba_boxel_frame, text="Пропустить боксель", fg="white")
        theme.register_alternate(
            (self.yoba_boxel_button, self.yoba_boxel_button_dark, self.yoba_boxel_button_dark),
            {"column": 1, "row": 0, "sticky": "EW"}
        )
        self.yoba_boxel_button.bind('<Button-1>', self.__yoba_next_boxel)
        theme.button_bind(self.yoba_boxel_button_dark, self.__yoba_next_boxel)

        self.yoba_calibrate2_frame = tk.Frame(self.yoba)
        self.yoba_calibrate2_frame.grid_columnconfigure(0, weight=1)
        self.yoba_calibrate_instructions_label = tk.Label(self.yoba_calibrate2_frame, text="Выделяйте системы бокселя в цель")
        self.yoba_calibrate_instructions_label.grid(column=0, row=0, sticky="W")

        self.yoba_copy_boxel_button = nb.Button(self.yoba_calibrate2_frame, text="Копировать боксель")
        self.yoba_copy_boxel_button_dark = tk.Label(self.yoba_calibrate2_frame, text="Копировать боксель", fg="white")
        theme.register_alternate(
            (self.yoba_copy_boxel_button, self.yoba_copy_boxel_button_dark, self.yoba_copy_boxel_button_dark),
            {"column": 1, "row": 0, "sticky": "EW"}
        )
        self.yoba_copy_boxel_button.bind('<Button-1>', self.__yoba_copy_boxel)
        theme.button_bind(self.yoba_copy_boxel_button_dark, self.__yoba_copy_boxel)

        self.yoba_next_frame = tk.Frame(self.yoba)
        self.yoba_next_frame.grid_columnconfigure(0, weight=1)
        self.__yoba_next_system_var = tk.StringVar(self.yoba_next_frame)
        self.yoba_next_system = tk.Label(self.yoba_next_frame, textvariable=self.__yoba_next_system_var)
        self.yoba_next_system.grid(column=0, row=0, sticky="W")

        self.yoba_copy = nb.Button(self.yoba_next_frame, text="Копировать систему")
        self.yoba_copy_dark = tk.Label(self.yoba_next_frame, text="Копировать систему", fg="white")
        theme.register_alternate(
            (self.yoba_copy, self.yoba_copy_dark, self.yoba_copy_dark),
            {"column": 1, "row": 0, "sticky": "EW"}
        )
        self.yoba_copy.bind('<Button-1>', self.__yoba_copy_button)
        theme.button_bind(self.yoba_copy_dark, self.__yoba_copy_button)

        self.yoba.grid_columnconfigure(0, weight=1)
        self.yoba.grid(row=1, sticky="NWSE")
        BasicThread(name="YobaDataReader", target=self.yoba_load).start()

        # упаковываем до данных по местоположению
        BasicThread(name="BioPatrolDataReader", target=self.load_data).start()
        self.grid(column=0, row=gridrow, sticky="NWSE")
        self.biopatrol.grid_columnconfigure(0, weight=1)
        self.biopatrol.grid(row=0, sticky="NWSE")
        self.set_status("Местоположение неизвестно.\nТребуется прыжок или перезапуск игры.")

        self.db = sqlite3.connect(Path(self.plugin_dir, "data", "biopatrol.db"), check_same_thread=False)
        self.db.execute("PRAGMA foreign_keys = ON")

    def brab_fun(self):
        self.is_brab_fun = True
        brab_gif = Path(self.plugin_dir, "icons", "brabroll.gif")
        info = Image.open(brab_gif)
        self.brab_label = tk.Label(self, image="")
        self.brab_label.grid(row=1, sticky="NWSE")

        brab_frames = []
        total_frames = info.n_frames
        for i in range(total_frames):
            frame = tk.PhotoImage(file=brab_gif, format=f'gif -index {i}')
            brab_frames.append(frame)

        def __inner(self, next_frame):
            next_frame = next_frame % total_frames
            brab_gif_frame = brab_frames[next_frame]
            self.brab_label.configure(image=brab_gif_frame)
            if self.is_brab_fun is True:
                self.after(30, __inner, self, next_frame + 1)

        self.after(0, __inner, self, 0)


    def stop_brab_fun(self):
        self.is_brab_fun = False
        self.brab_label.grid_remove()


    def read_old_logs(self):
        pattern = re.compile(r"^Journal\.20\d\d-(?:0[1-9]|1[0-2])-(?:0[1-9]|[1-2][0-9]|[3][01])T(?:[01][0-9]|2[0-3])(?:[0-5][0-9]){2}\.\d\d\.log$")    # noqa: E501
        logsdir_default = Path.home() / "Saved Games/Frontier Developments/Elite Dangerous"
        logsdir = Path(_edmc_config.get_str("journaldir") or logsdir_default)
        logs = [
            logfile
            for logfile in logsdir.iterdir()
            if (
                logfile.is_file()
                and re.match(pattern, logfile.name) is not None
            )
        ]
        debug(f"Game logs dir: {logsdir} ({len(logs)} files found)")
        self.db.execute("DELETE FROM data_cmdrs")

        class BioPatrolJournalProcessor:
            def __init__(self, logs: list[Path], patrol: BioPatrol):
                super().__init__()
                self.logs = logs
                self.patrol = patrol

            def run(self):
                coords = Coords(0, 0, 0)
                system = ""
                body = None
                self.patrol.brab_fun()
                for file in logs:
                    self.patrol.set_status(f"Выпрямляем логи: {os.path.basename(file)}...")
                    with open(file, 'r', encoding="utf-8") as f:
                        lines = f.readlines()
                    for line in lines:
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            # skip broken file
                            break
                        if (
                            self.patrol.last_processed_timestamp        # None if this is run on startup
                            and datetime.fromisoformat(data["timestamp"]) > self.patrol.last_processed_timestamp
                        ):
                            # we reached fresh data - better leave it for the normal mode to process
                            self.patrol.stop_brab_fun()
                            return
                        if "StarPos" in data:
                            coords = Coords(x=data["StarPos"][0], y=data["StarPos"][1], z=data["StarPos"][2])
                        if data["event"] == "Location":
                            system = data["StarSystem"]
                            body = data.get("Body", None)
                        elif data["event"] == "FSDJump":
                            system = data["StarSystem"]
                            body = None
                        elif data["event"] == "Disembark":
                            body = data["Body"]
                        elif data["event"] == "Embark":
                            body = None
                        entry = BioPatrolJournalEntry(
                            system=system,
                            data=data,
                            coords=coords,
                            body=body
                        )
                        self.patrol.on_historic_entry(entry)
                self.patrol.stop_brab_fun()

        BioPatrolJournalProcessor(logs, self).run()
        debug("Finished reading old game logs")


    def filter_predictions_after_dss(self, system_id64, bodyid, genuses):
        for i in self.db.execute("SELECT DISTINCT genus, body FROM predictions_data WHERE system_id64 = ? AND bodyid = ?", (system_id64, bodyid,)):
            predicted_genus = i[0]
            planet = i[1]
            if predicted_genus not in genuses:
                debug(f">> Removing {predicted_genus} prediction for {planet} - ruled out by DSS")
                self.db.execute("UPDATE predictions_data SET status = -1 WHERE system_id64 = ? AND bodyid = ? AND genus = ?", (system_id64, bodyid, predicted_genus,))


    def open_discoveries(self):
        self.db.execute('''
        CREATE TABLE IF NOT EXISTS data_cmdrs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fid TEXT NOT NULL,
            name TEXT NOT NULL,
            prev_id INT NOT NULL
        )
        ''')
        self.db.execute('''
        CREATE TABLE IF NOT EXISTS data_systems (
            id64 INT NOT NULL,
            name TEXT NOT NULL,
            cmdr_id INT NOT NULL,
            procgen_sector_id INT NOT NULL,
            procgen_masscode_id INT NOT NULL,
            procgen_boxel_id INT NOT NULL,
            procgen_system_id INT NOT NULL,
            PRIMARY KEY (id64, cmdr_id),
            FOREIGN KEY (cmdr_id) REFERENCES data_cmdrs(id) ON DELETE CASCADE
        )
        ''')
        self.db.execute('''
        CREATE TABLE IF NOT EXISTS data_planets (
            system_id64 INT NOT NULL,
            bodyid INT NOT NULL,
            name TEXT NOT NULL,
            cmdr_id INT NOT NULL,
            biosignals INT DEFAULT NULL,
            PRIMARY KEY (system_id64, bodyid, cmdr_id),
            FOREIGN KEY (system_id64, cmdr_id) REFERENCES data_systems(id64, cmdr_id) ON DELETE CASCADE
        )
        ''')
        self.db.execute('''
        CREATE TABLE IF NOT EXISTS data_bios (
            system_id64 INT NOT NULL,
            bodyid INT NOT NULL,
            genus TEXT NOT NULL,
            species TEXT,
            cmdr_id INT NOT NULL,
            PRIMARY KEY (system_id64, bodyid, genus, cmdr_id),
            FOREIGN KEY (system_id64, bodyid, cmdr_id) REFERENCES data_planets(system_id64, bodyid, cmdr_id) ON DELETE CASCADE
        )
        ''')


    def open_predictions(self):
        self.db.execute('''
        CREATE TABLE IF NOT EXISTS predictions_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        ''')
        # status:
        # 0  - unknown
        # 1  - confirmed
        # -1 - falsified
        self.db.execute('''
        CREATE TABLE IF NOT EXISTS predictions_data (
            species TEXT NOT NULL,
            genus TEXT NOT NULL,
            system_id64 INT NOT NULL,
            bodyid INT NOT NULL,
            system TEXT NOT NULL,
            body TEXT NOT NULL,
            x FLOAT NOT NULL,
            y FLOAT NOT NULL,
            z FLOAT NOT NULL,
            region TEXT NOT NULL,
            priority INT NOT NULL,
            status INT NOT NULL DEFAULT 0,
            PRIMARY KEY (species, system_id64, bodyid)
        )
        ''')


    def unpack_predictions(self):
        try:
            with gzip.open(Path(self.plugin_dir, "data", self.FILENAME_RAW), 'r') as f:
                return json.load(f)
        except Exception:
            self.set_status(f"Архив с предсказаниями не найден или повреждён (data/{self.FILENAME_RAW})")
            return {}


    def update_predictions(self):
        archived = self.unpack_predictions()
        archived_date = archived.get("timestamp", "1970-01-01")

        self.open_predictions()
        unpacked_date = self.db.execute("SELECT key FROM predictions_metadata WHERE key = 'timestamp'").fetchone()
        if unpacked_date is None:
            unpacked_date = ("1970-01-01",)

        # check if archive is newer
        if archived and (unpacked_date[0] < archived_date):
            self.process_archive_data(archived)
            self.save_data()
            return True
        else:
            return False


    def cleanup_predictions(self):
        self.set_status(f"Отфильтровываем находки КМДР...")

        for i in self.db.execute("SELECT system_id64, bodyid, name FROM data_planets GROUP BY system_id64, bodyid"):
            system_id64 = i[0]
            bodyid = i[1]
            planet = i[2]

            actual_genuses = set()
            for j in self.db.execute("SELECT genus, species, cmdr_id FROM data_bios WHERE system_id64 = ? AND bodyid = ?", (system_id64, bodyid,)):
                genus = j[0]
                signal = j[1]
                cmdr_id = j[2]

                if signal is not None:
                    self.process_genus_bio(genus, signal, system_id64, bodyid, cmdr_id)

                actual_genuses.add(genus)

            self.filter_predictions_after_dss(system_id64, bodyid, actual_genuses)


    def load_data(self):
        with self.__threadlock:
            while True:
                try:
                    self.after(0, lambda: None)
                except RuntimeError:        # tk isn't ready
                    sleep(1)
                else:
                    break

            self.open_discoveries()

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

            predictions_updated = self.update_predictions()

            self._enabled = True

            if self.db.execute("SELECT COUNT(id) FROM data_cmdrs").fetchone()[0] == 0:
                debug(f"Database not found; reading old game logs")
                self.read_old_logs()

            self.cleanup_predictions()

        self.__live_data = True
        self.save_data()
        self.set_status("Данные импортированы.\nТребуется прыжок или перезапуск игры.")


    def process_archive_data(self, raw_data: dict):
        # remove outdated predictions
        self.db.execute("DELETE FROM predictions_data")

        for region, region_data in raw_data["bio"].items():
            for species, species_data in region_data.items():

                for location in species_data["locations"]:
                    self.db.execute('''
                    INSERT INTO predictions_data
                        (species, genus, system_id64, bodyid, system, body, x, y, z, region, priority)
                    VALUES
                        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        species,
                        species.split()[0],
                        location["system_id64"],
                        location["body_id64"] >> 55,
                        location["system"],
                        location["body"],
                        location["x"],
                        location["y"],
                        location["z"],
                        region,
                        species_data["priority"],
                        ))

            self.set_status(f"Обработан регион {region}")

        timestamp = raw_data.get("timestamp", "1970-01-01")
        self.db.execute("INSERT OR REPLACE INTO predictions_metadata (key, value) VALUES (?, ?)", ("timestamp", timestamp))


    def save_data(self):
        if not self.__live_data:
            return

        self.db.commit()


    def process_genus_bio(self, genus, bioname, system_id64, bodyid, cmdr_id=None, report=False, entry_region=None):
        if cmdr_id is None:
            cmdr_id = self.cmdr_id

        planet = self.get_current_body(system_id64, bodyid, cmdr_id)

        # sanity check
        for codex_name, english_name in codex_to_english_variants.items():
            if bioname in (codex_name, english_name):
                break
        else:
            debug(f'>> Warning: {bioname} is not in dictionary')

        region = None
        priority = 1

        res = self.db.execute("SELECT region, priority FROM predictions_data WHERE species = ? AND system_id64 = ? AND bodyid = ?", (bioname, system_id64, bodyid,)).fetchone()
        if res is not None:
            region, priority = res

        # We may know the region from CodexEntry event
        if entry_region is not None:
            region = entry_region

        if report is True and self.__live_data:
            url_params = {
                "entry.1220081267": self.cmdr,
                "entry.82601913": region,
                "entry.1533043520": planet,
                "entry.1614339748": get_priority_text(priority),
                "entry.393624172": bioname
            }
            url = f'{URL_GOOGLE}/1FAIpQLSfp4rPNSOVf5V-LYLEUXCKomDBaHo92lPwfp9YJDrml2QGUQQ/formResponse?usp=pp_url&{"&".join([f"{k}={v}" for k, v in url_params.items()])}'
            Reporter(url).start()

        debug(f"Found {bioname} (genus: {genus}) at {planet} (priority: {priority})")

        # confirm prediction
        self.db.execute("UPDATE predictions_data SET status = 1 WHERE system_id64 = ? AND bodyid = ? AND species = ?", (system_id64, bodyid, bioname, ))

        # only one species per genus allowed
        self.db.execute("UPDATE predictions_data SET status = -1 WHERE status = 0 AND system_id64 = ? AND bodyid = ? AND genus = ? AND species != ?", (system_id64, bodyid, genus, bioname, ))

        # all signals has been found
        signals_found = self.db.execute("SELECT COUNT(DISTINCT(species)) FROM data_bios WHERE system_id64 = ? AND bodyid = ? AND cmdr_id = ? AND species IS NOT NULL", (system_id64, bodyid, cmdr_id, )).fetchone()[0]

        signals_count = self.db.execute("SELECT biosignals FROM data_planets WHERE system_id64 = ? AND bodyid = ? AND cmdr_id = ?", (system_id64, bodyid, cmdr_id, )).fetchone()[0]
        if signals_found == signals_count:
            self.db.execute("UPDATE predictions_data SET status = -1 WHERE status = 0 AND system_id64 = ? AND bodyid = ?", (system_id64, bodyid, ))

        # don't update ui on EDMC startup
        if self._enabled:
            self.update_pos()

        # new regional codex entry
        self.db.execute("UPDATE predictions_data SET priority = 1 WHERE priority = 2 AND species = ? AND region = ?", (bioname, region, ))

        # new galactic new codex entry
        self.db.execute("UPDATE predictions_data SET priority = 1 WHERE priority = 3 AND species = ? AND region = ?", (bioname, region, ))
        self.db.execute("UPDATE predictions_data SET priority = 2 WHERE priority = 3 AND species = ? AND region != ?", (bioname, region, ))


    def biofound_init_body(self, system_id64, bodyid, signal_count=None):
        self.db.execute("UPDATE data_planets SET biosignals = ? WHERE system_id64 = ? AND bodyid = ? AND cmdr_id = ?", (signal_count, system_id64, bodyid, self.cmdr_id, ))


    def biofound_set_genuses(self, system_id64, bodyid, genuses):
        for i in genuses:
            self.biofound_add_genus(system_id64, bodyid, i)


    def biofound_add_genus(self, system_id64, bodyid, genus):
        self.db.execute('''
            INSERT OR IGNORE INTO
                data_bios
            (system_id64, bodyid, genus, cmdr_id)
                VALUES
            (?, ?, ?, ?)''', (system_id64, bodyid, genus, self.cmdr_id))


    def biofound_add_signal(self, system_id64, bodyid, signal):
        genus = signal.split()[0]
        self.biofound_add_genus(system_id64, bodyid, genus)
        self.db.execute("UPDATE data_bios SET species = ? WHERE system_id64 = ? AND bodyid = ? AND cmdr_id = ? AND genus = ?", (signal, system_id64, bodyid, self.cmdr_id, genus))


    def get_current_body(self, system_id64, bodyid, cmdr_id):
        res = self.db.execute("SELECT name FROM data_planets WHERE system_id64 = ? AND bodyid = ? AND cmdr_id = ?", (system_id64, bodyid, cmdr_id)).fetchone()
        return None if res is None else res[0]

    def get_system(self, id64, cmdr_id):
        row = self.db.execute("SELECT name FROM data_systems WHERE id64 = ? AND cmdr_id = ?", (id64, cmdr_id,))
        if row is None:
            return None
        else:
            return row.fetchone()[0]

    def store_current_system(self, entry):
        # store some procgen data
        sector_id, masscode_id, boxel_id, system_id, _ = split_ids(entry.data["SystemAddress"])
        self.current_system_id64 = entry.data["SystemAddress"]

        self.db.execute('''
            INSERT OR IGNORE INTO
                data_systems
            (id64, name, cmdr_id, procgen_sector_id, procgen_masscode_id, procgen_boxel_id, procgen_system_id)
                VALUES
            (?, ?, ?, ?, ?, ?, ?)
        ''', (entry.data["SystemAddress"], entry.data["StarSystem"], self.cmdr_id, sector_id, masscode_id, boxel_id, system_id,))


    def store_current_body(self, entry, name):
        self.db.execute('''
            INSERT OR IGNORE INTO
                data_planets
            (system_id64, bodyid, name, cmdr_id)
                VALUES
            (?, ?, ?, ?)
        ''', (entry.data["SystemAddress"], entry.data["BodyID"], name, self.cmdr_id))


    def on_historic_entry(self, entry: BioPatrolJournalEntry):
        self.process_entry(entry)


    def on_journal_entry(self, entry: JournalEntry):
        with self.__threadlock:
            self.process_entry(entry)


    def process_entry(self, entry):
        if self.__live_data:
            self.last_processed_timestamp = datetime.fromisoformat(entry.data["timestamp"])
        required_events = ["NewCommander", "Commander", "Location", "FSDJump", "Scan", "ScanOrganic", "SAASignalsFound", "FSSBodySignals", "FSSAllBodiesFound", "CodexEntry", "SupercruiseExit", "FSDTarget"]
        if not self.__live_data:
            required_events += ["ApproachBody", "Touchdown", "Disembark"]

        event = entry.data["event"]
        if event not in required_events:
            return

        if not self._enabled:       # на случай, если попытка чтения данных завершилась ошибкой
            return

        if event == "NewCommander":
            self.db.execute("INSERT INTO data_cmdrs (fid, name, prev_id) VALUES (?, ?, 0)", (entry.data["FID"], entry.data["Name"]))

        elif event == "Commander":
            c_fid = entry.data["FID"]
            c_name = entry.data["Name"]

            row = self.db.execute("SELECT id, name, prev_id FROM data_cmdrs WHERE fid = ? ORDER BY id DESC LIMIT 1", (c_fid, )).fetchone()
            if row is None: # no such CMDR
                self.db.execute("INSERT INTO data_cmdrs (fid, name, prev_id) VALUES (?, ?, 0)", (c_fid, c_name,))
            elif row[1] != c_name: # changed name
                self.db.execute("INSERT INTO data_cmdrs (fid, name, prev_id) VALUES (?, ?, ?)", (c_fid, c_name, row[0]))

            row = self.db.execute("SELECT id FROM data_cmdrs WHERE fid = ? ORDER BY id DESC LIMIT 1", (c_fid, )).fetchone()
            self.cmdr_id = row[0]

            self.yoba_update_status()
            self.yoba_update()

        elif event == "Scan":
            self.store_current_system(entry)

        elif event in ("Location", "FSDJump"):
            self.__update_data_wrap(entry)
            self.store_current_system(entry)

            if event == "Location":
                self.store_current_body(entry, entry.data["Body"])

        elif event == "ScanOrganic":
            body = self.get_current_body(entry.data["SystemAddress"], entry.data["Body"], self.cmdr_id)

            genus = codex_to_english_genuses.get(entry.data["Genus"], entry.data["Genus"])
            if "Variant" not in entry.data:
                return

            bioname = codex_to_english_variants.get(entry.data["Variant"], entry.data["Variant"])
            if self.__live_data:
                self.set_status(f"Scanned {bioname} at {body}")

            self.biofound_init_body(entry.data["SystemAddress"], entry.data["Body"])
            self.biofound_add_signal(entry.data["SystemAddress"], entry.data["Body"], bioname)

            # update data
            self.process_genus_bio(genus, bioname, entry.data["SystemAddress"], entry.data["Body"], report=True)

            self.save_data()
            self.__update_data_wrap(entry)

        elif event == "CodexEntry":
            if "BodyID" not in entry.data:
                return

            if entry.data["SubCategory"] != "$Codex_SubCategory_Organic_Structures;":
                return

            body = self.get_current_body(entry.data["SystemAddress"], entry.data["BodyID"], self.cmdr_id)

            bioname = codex_to_english_variants.get(entry.data["Name"], entry.data["Name"])
            region = codex_to_english_regions.get(entry.data["Region"], entry.data["Region"])

            # HACK -- CodexEntry does not have 'Genus' key
            genus = bioname.split()[0]
            if self.__live_data:
                self.set_status(f"Scanned {bioname} at {body}")

            self.biofound_init_body(entry.data["SystemAddress"], entry.data["BodyID"])
            self.biofound_add_signal(entry.data["SystemAddress"], entry.data["BodyID"], bioname)

            # update data
            self.process_genus_bio(genus, bioname, entry.data["SystemAddress"], entry.data["BodyID"], report=True, entry_region=region)

            self.save_data()
            self.__update_data_wrap(entry)

        elif event == "SAASignalsFound" and entry.data.get("Genuses"):
            genuses = [codex_to_english_genuses.get(i["Genus"], i["Genus"]) for i in entry.data["Genuses"]]
            bodyName = entry.data["BodyName"]

            self.store_current_body(entry, bodyName)

            self.biofound_init_body(entry.data["SystemAddress"], entry.data["BodyID"], len(genuses))
            self.biofound_set_genuses(entry.data["SystemAddress"], entry.data["BodyID"], genuses)

            self.filter_predictions_after_dss(entry.data["SystemAddress"], entry.data["BodyID"], genuses)

            self.__update_data_wrap(entry)
            self.update_pos()
            self.save_data()

        elif event == "FSSBodySignals":
            name = entry.data["BodyName"]
            for signal in entry.data.get("Signals", []):
                if signal["Type"] == "$SAA_SignalType_Biological;":
                    self.signals_in_system[name] = signal["Count"]
                    self.biofound_init_body(entry.data["SystemAddress"], entry.data["BodyID"], signal["Count"])

        elif event == "FSSAllBodiesFound":
            for i in self.db.execute("SELECT DISTINCT bodyid, body FROM predictions_data WHERE system_id64 = ?", (entry.data["SystemAddress"], )):
                bodyid = i[0]
                planet = i[1]
                if bodyid not in self.signals_in_system:

                    j = self.db.execute("SELECT biosignals FROM data_planets WHERE system_id64 = ? AND bodyid = ? AND cmdr_id = ?", (entry.data["SystemAddress"], bodyid, self.cmdr_id,)).fetchone()
                    if j is not None:
                        signalCount = j[0]
                        if signalCount > 0:
                            debug(f'>> Keeping {planet}: has {signalCount} signals')
                            continue

                        debug(f'>> Removing {planet}: known to have no signals')

                    debug(f'>> Removing {planet}: has no signals')
                    self.biofound_init_body(entry.data["SystemAddress"], bodyid, 0)
                    self.db.execute("UPDATE predictions_data SET status = -1 WHERE status = 0 AND system_id64 = ? AND bodyid = ?", (entry.data["SystemAddress"], bodyid, ))

            self.__update_data_wrap(entry)
            self.update_pos()
            self.save_data()
            self.signals_in_system.clear()

        elif event == "SupercruiseExit":
            self.store_current_system(entry)
            self.store_current_body(entry, entry.data["Body"])
        elif event == "ApproachBody":
            self.store_current_body(entry, entry.data["Body"])
        elif event in ("Touchdown", "Disembark"):
            if entry.data["OnPlanet"]:
                self.store_current_body(entry, entry.data["Body"])
        elif event == "FSDTarget":
            if self.yoba_calibrating:
                # sanity check
                target_id64 = entry.data["SystemAddress"]
                boxel_data = self.yoba_boxels[self.yoba_current_boxel]

                sector_id, masscode_id, boxel_id, system_id, _ = split_ids(target_id64)
                if sector_id == boxel_data["_sector"] and masscode_id == boxel_data["_masscode"] and boxel_id == boxel_data["_boxel"]:
                    if boxel_data["start"] is None or system_id < boxel_data["start"]:
                        boxel_data["start"] = system_id
                        self.yoba_update()

                    if boxel_data["finish"] is None or system_id > boxel_data["finish"]:
                        boxel_data["finish"] = system_id
                        self.yoba_update()


    def on_dashboard_entry(self, cmdr, is_beta, entry):
        if self.cmdr != cmdr and cmdr is not None:
            self.cmdr = cmdr

    def set_status(self, text: str):
        def __inner():
            self.switch_frame.grid_remove()
            self.region_frame.grid_remove()
            self.closest_location_frame.grid_remove()
            self.buttons_frame.grid_remove()
            # we should always have `filter_frame` mapped because the user needs the ability
            # to change regions in case there are no suitable locations in the current selection
            self.filter_frame.grid()
            self.__dummy_var.set(text)
            self.dummy_label.grid()
        self.after(0, __inner)


    def show(self):
        def __inner():
            self.dummy_label.grid_remove()
            self.switch_frame.grid()
            self.region_frame.grid()
            self.closest_location_frame.grid()
            self.buttons_frame.grid()
            self.filter_frame.grid()
        self.after(0, __inner)


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
        return [row[0] for row in self.db.execute("SELECT DISTINCT(species) FROM predictions_data WHERE status == 0")]


    def __update_data_wrap(self, entry):
        if not self.__live_data:
            return

        self.__update_data(entry)


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
            locations = {}
            for row in self.db.execute("SELECT body, system, x, y, z, region, priority FROM predictions_data WHERE status = 0 AND species = ?", (bio_item, )):
                locations[row[0]] = {
                    "system" : row[1],
                    "x" : row[2],
                    "y" : row[3],
                    "z" : row[4],
                    "region" : row[5],
                    "priority" : row[6],
                }

            closest_location = get_closest(current_coords, locations)
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
        if not self.__live_data:
            return

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

        row = self.db.execute("SELECT biosignals FROM data_planets WHERE name = ? AND cmdr_id = ?", (planet, self.cmdr_id, )).fetchone()
        if row is None:
            self.set_status(f"Сначала просканируйте {planet} с помощью DSS.")
            self.after(3000, self.show)
            return

        self.db.execute("UPDATE predictions_data SET status = -1 WHERE status = 0 AND planet = ?", (planet, ))
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
        if self.current_coords:
            self.__update_data_coords(self.current_coords)


    @property
    def yoba_boxels(self):
        return self.__yoba_boxels


    @yoba_boxels.setter
    def yoba_boxels(self, value):
        if value == self.__yoba_boxels:
            return
        self.__yoba_boxels = value
        self.yoba_update_status()


    @property
    def yoba_calibrating(self):
        return self.__yoba_calibrating


    @yoba_calibrating.setter
    def yoba_calibrating(self, value):
        if value == self.__yoba_calibrating:
            return
        self.__yoba_calibrating = value
        self.yoba_update_status()


    def yoba_load(self):
        try:
            with open(Path(self.plugin_dir, "data", self.FILENAME_BOXELS), 'r') as f:
                self.yoba_boxels = json.load(f)
        except Exception:
            self.yoba_boxels = None

        self.yoba_update_status()


    def yoba_save(self):
        with open(Path(self.plugin_dir, "data", self.FILENAME_BOXELS), 'w') as f:
            if self.yoba_boxels is not None:
                json.dump(self.yoba_boxels, f, ensure_ascii=False)

        self.yoba_update()


    def yoba_update_status(self):
        if self.cmdr_id is None:
            self.yoba_set_status(YobaStatus.IDLE)
            return

        if self.__yoba_boxels is None:
            self.yoba_set_status(YobaStatus.IDLE)
            return

        if self.__yoba_calibrating:
            self.yoba_set_status(YobaStatus.CALIBRATING)
        else:
            self.yoba_set_status(YobaStatus.RUNNING)


    def yoba_update(self):
        if self.cmdr_id is None:
            return

        if self.yoba_boxels is None:
            return

        for boxel, data in self.yoba_boxels.items():
            if data["surveyed"] == True:
                continue

            self.yoba_current_boxel = boxel
            break
        else:
            self.__yoba_finish()
            return

        boxel_data = self.yoba_boxels[self.yoba_current_boxel]
        if boxel_data["start"] is None or boxel_data["finish"] is None:
            self.yoba_calibrating = True

        start = boxel_data["start"] if boxel_data["start"] is not None else "?"
        finish = boxel_data["finish"] if boxel_data["finish"] is not None else "?"

        if self.yoba_calibrating:
            self.__yoba_stop_var.set(f"Боксель {self.yoba_current_boxel}")

            # always start with zero, except for h masscode
            if boxel_data["_masscode"] != 7:
                boxel_data["start"] = 0

            # initialize with current system
            sector_id, masscode_id, boxel_id, system_id, _ = split_ids(self.current_system_id64)
            if sector_id == boxel_data["_sector"] and masscode_id == boxel_data["_masscode"] and boxel_id == boxel_data["_boxel"]:
                boxel_data["start"] = boxel_data["finish"] = system_id

            self.__yoba_calibrate_var.set(f'Калибровка: {start}-{finish}')
        else:
            self.__yoba_stop_var.set(f"Боксель {self.yoba_current_boxel}")

            # getting systems
            known_systems = set()
            for row in self.db.execute("SELECT procgen_system_id FROM data_systems WHERE procgen_sector_id = ? AND procgen_masscode_id = ? AND procgen_boxel_id = ? AND cmdr_id = ?",
                                            (boxel_data["_sector"], boxel_data["_masscode"], boxel_data["_boxel"], self.cmdr_id)):
                known_systems.add(row[0])

            first_unknown = None
            for i in range(start, finish + 1):
                if i not in known_systems:
                    first_unknown = i
                    break
            else:
                self.yoba_skip_boxel()

            boxelmap_string = ""
            boxelmap_range = 10
            boxelmap_ellipsis_1 = False
            boxelmap_ellipsis_2 = False
            for i in range(start, finish + 1):
                if first_unknown - i > boxelmap_range:
                    boxelmap_ellipsis_1 = True
                    continue

                if i - first_unknown > boxelmap_range:
                    boxelmap_ellipsis_2 = True
                    continue

                if i == first_unknown:
                    boxelmap_string += "▲"
                    continue

                if i in known_systems:
                    boxelmap_string += "█"
                else:
                    boxelmap_string += "▓"

            if boxelmap_ellipsis_1:
                boxelmap_string = f"…{boxelmap_string}"

            if boxelmap_ellipsis_2:
                boxelmap_string = f"{boxelmap_string}…"
            self.__yoba_boxel_var.set(f'{start} [{boxelmap_string}] {finish}')
            self.__yoba_next_system_var.set(f'{get_procgen_name(boxel_data["_sector"], boxel_data["_masscode"], boxel_data["_boxel"], first_unknown)}')


    def yoba_set_status(self, status):
        self.yoba_status = status

        match status:
            case YobaStatus.IDLE:
                self.yoba_start_frame.grid(row=0, sticky="NWSE")
                self.yoba_stop_frame.grid_remove()

                self.yoba_calibrate_frame.grid_remove()
                self.yoba_boxel_frame.grid_remove()

                self.yoba_next_frame.grid_remove()
                self.yoba_calibrate2_frame.grid_remove()
            case YobaStatus.CALIBRATING:
                self.yoba_start_frame.grid_remove()
                self.yoba_stop_frame.grid(row=0, sticky="NWSE")

                self.yoba_calibrate_frame.grid(row=1, sticky="NWSE")
                self.yoba_boxel_frame.grid_remove()

                self.yoba_next_frame.grid_remove()
                self.yoba_calibrate2_frame.grid(row=2, sticky="NWSE")
            case YobaStatus.RUNNING:
                self.yoba_start_frame.grid_remove()
                self.yoba_stop_frame.grid(row=0, sticky="NWSE")

                self.yoba_calibrate_frame.grid_remove()
                self.yoba_boxel_frame.grid(row=1, sticky="NWSE")

                self.yoba_next_frame.grid(row=2, sticky="NWSE")
                self.yoba_calibrate2_frame.grid_remove()

        self.yoba_save()

    def yoba_setup_boxel(self, settings):
        sector_id, masscode_id, boxel_id, system_id, _ = split_ids(self.current_system_id64)

        yoba_boxels = {}
        parents = []
        children = []

        parents.append((masscode_id, boxel_id))
        for i in range(settings["depth"] + 1):
            for parent in parents:
                parent_masscode, parent_boxel = parent

                name = f'{get_sector(sector_id)} {get_boxel(parent_masscode, parent_boxel)}'
                obj = {
                    "_sector" : sector_id,
                    "_masscode" : parent_masscode,
                    "_boxel" : parent_boxel,
                    "surveyed" : False,
                    "start" : None,
                    "finish" : None
                }
                yoba_boxels[name] = obj

                if i < settings["depth"]:
                    for child in get_children(parent_masscode, parent_boxel):
                        child_masscode, child_boxel = child
                        children.append((child_masscode, child_boxel))

            parents = children.copy()
            children.clear()

        self.yoba_boxels = yoba_boxels


    def yoba_skip_boxel(self):
        self.yoba_boxels[self.yoba_current_boxel]["surveyed"] = True
        self.yoba_save()


    def __yoba_calibrate(self, event: tk.Event):
        boxel_data = self.yoba_boxels[self.yoba_current_boxel]
        if boxel_data["start"] is None or boxel_data["finish"] is None:
            return

        self.yoba_calibrating = False


    def __yoba_next_boxel(self, event: tk.Event):
        self.yoba_skip_boxel()


    def __yoba_copy_boxel(self, event: tk.Event):
        copyclip(f'{self.yoba_current_boxel}-')


    def __yoba_abort(self, event: tk.Event):
        self.yoba_boxels = None
        self.yoba_calibrating = False

        self.__yoba_start_var.set("Исследование прервано")
        self.yoba_save()


    def __yoba_finish(self):
        self.yoba_boxels = None
        self.yoba_calibrating = False

        self.__yoba_start_var.set("Исследование завершено")
        self.yoba_save()


    def __yoba_window_create(self, event: tk.Event):
        if self.__yoba_window is not None:
            return
        if self.current_system_id64 is None:
            return
        if self.yoba_boxels is not None:
            return

        sector_id, masscode_id, boxel_id, system_id, _ = split_ids(self.current_system_id64)
        system_data = {
            "id64" : self.current_system_id64,
            "name" : self.get_system(self.current_system_id64, self.cmdr_id),
            "pgname" : get_procgen_name(sector_id, masscode_id, boxel_id, system_id)
        }

        self.__yoba_window = YobaWindow(self, self.__yoba_window_callback, system_data)
        self.__yoba_window.protocol("WM_DELETE_WINDOW", self.__yoba_window_closed)


    def __yoba_window_closed(self):
        self.__yoba_window.destroy()
        self.__yoba_window = None


    def __yoba_window_callback(self, settings):
        self.yoba_setup_boxel(settings)

        self.__yoba_window.destroy()
        self.__yoba_window = None


    def __yoba_copy_button(self, event):
        copyclip(self.__yoba_next_system_var.get())


    def __on_old_logs_processing_requested(self, event: tk.Event):
        answer = askyesno(
            title="Чтение старых логов",
            message=(
                "Запустить чтение старых логов?\n\n"
                "Это действие приведёт к сбросу текущих био-данных плагина и начнёт обработку "
                "всех игровых логов, сохранённых на устройстве. Этот процесс займёт некоторое время. "
                "Данная опция может восстановить ваши старые и пропущенные находки, но они "
                "не будут отправлены в общую таблицу открытий.\n\n"
                "Продолжить?"
            ),
            icon=WARNING
        )
        if not answer:
            return

        def inner():
            # this will block JournalProcessor, so the whole plugin will be waiting for us to finish
            # (won't affect EDMC and other plugins)
            with self.__threadlock:
                self.__live_data = False
                self.read_old_logs()
                self.cleanup_predictions()
                self.__live_data = True
                self.save_data()
            debug("Finished reading old logs.")
            if self.current_coords:
                self.show()

        debug("Processing of old logs was requested by the user.")
        BasicThread(name="BioPatrolOldLogsReader", target=inner).start()
