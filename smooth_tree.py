# SPDX-License-Identifier: AGPL-3.0-only
"""Pixel-scrolled, virtual tree rows backed by Tk's standard tree data model."""

import math
import tkinter as tk
from tkinter import ttk, font as tkfont
from functools import lru_cache


class PixelTreeview(tk.Canvas):
    def __init__(
        self,
        parent,
        columns=(),
        displaycolumns=None,
        show="tree",
        selectmode="browse",
        style="Treeview",
        height=1,
        **options,
    ):
        self.scale = float(parent.tk.call("tk", "scaling")) / (96 / 72)
        self.books = style == "Books.Treeview"
        self.headers = "headings" in show
        self.row_height = max(1, int((82 if self.books else 33) * self.scale))
        self.header_height = int(27 * self.scale) if self.headers else 0
        self.offset = 0.0
        self.target = 0.0
        self.animation = None
        self.redraw = None
        self._pending_see = None
        self._visible = None
        self._indices = {}
        self._scroll_command = None
        self._tag_styles = {}
        super().__init__(
            parent,
            width=1,
            height=max(1, height * self.row_height),
            bg="#FFFFFF",
            bd=0,
            highlightthickness=0,
            takefocus=True,
            **options,
        )
        args = dict(columns=columns, show=show, selectmode=selectmode, style=style)
        if displaycolumns is not None:
            args["displaycolumns"] = displaycolumns
        self._timer_owner = self.winfo_toplevel()
        self.model = ttk.Treeview(
            parent, **args
        )  # Unmanaged data model, outside the Canvas clipping region.
        self.face = tkfont.Font(self, font=("Microsoft YaHei UI", 10))
        self.small = tkfont.Font(self, font=("Microsoft YaHei UI", 9))
        self.bold = tkfont.Font(self, font=("Microsoft YaHei UI", 10, "bold"))
        self._fit = lru_cache(maxsize=768)(self._fit_text)
        # App bindings run first, then internal behavior, just like ttk's class bindings.
        self._event_tag = "PixelTree:" + str(self)
        tags = self.bindtags()
        self.bindtags((tags[0], self._event_tag) + tags[1:])
        events = {
            "<Configure>": self._resize,
            "<Button-1>": self._click,
            "<Double-Button-1>": lambda event: "break",
            "<MouseWheel>": self.wheel,
            "<Button-4>": lambda event: self._wheel_steps(-1),
            "<Button-5>": lambda event: self._wheel_steps(1),
            "<Up>": lambda event: self._navigate(-1),
            "<Down>": lambda event: self._navigate(1),
            "<Home>": lambda event: self._navigate("home"),
            "<End>": lambda event: self._navigate("end"),
            "<Prior>": lambda event: self._navigate(
                -max(1, int(self.viewport() / self.row_height))
            ),
            "<Next>": lambda event: self._navigate(max(1, int(self.viewport() / self.row_height))),
            "<Left>": lambda event: self._branch(False),
            "<Right>": lambda event: self._branch(True),
            "<Return>": lambda event: self._activate(),
            "<space>": lambda event: self._activate(),
        }
        for sequence, callback in events.items():
            self.bind_class(self._event_tag, sequence, callback)
        self.bind("<Destroy>", self._dispose, add="+")

    def _dispose(self, event):
        if event.widget != self:
            return
        for token in (self.redraw, self.animation):
            if token is not None:
                try:
                    self._timer_owner.after_cancel(token)
                except tk.TclError:
                    pass
        self._fit.cache_clear()

    def configure(self, cnf=None, **options):
        if "yscrollcommand" in options:
            self._scroll_command = options.pop("yscrollcommand")
        result = super().configure(cnf, **options)
        if hasattr(self, "model"):
            self._schedule()
        return result

    config = configure

    def _schedule(self, structure=False):
        if structure:
            self._visible = None
        if self.redraw is None:
            self.redraw = self._timer_owner.after_idle(self._draw)

    def _rows(self):
        if self._visible is None:
            rows = []
            stack = [(item, 0) for item in reversed(self.model.get_children(""))]
            while stack:
                item, depth = stack.pop()
                rows.append((item, depth))
                if self.model.item(item, "open"):
                    stack.extend(
                        (child, depth + 1) for child in reversed(self.model.get_children(item))
                    )
            self._visible = rows
            self._indices = {item: index for index, (item, _) in enumerate(rows)}
        return self._visible

    def insert(self, parent, index, iid=None, **options):
        result = self.model.insert(parent, index, iid=iid, **options)
        self._schedule(True)
        return result

    def delete(self, *items):
        if items:
            self.model.delete(*items)
            self._schedule(True)

    def get_children(self, item=""):
        return self.model.get_children(item)

    def exists(self, item):
        return self.model.exists(item)

    def parent(self, item):
        return self.model.parent(item)

    def item(self, item, option=None, **options):
        result = self.model.item(item, option, **options)
        if options:
            self._schedule("open" in options)
        return result

    def set(self, item, column=None, value=None):
        result = self.model.set(item, column, value)
        if value is not None:
            self._schedule()
        return result

    def column(self, column, option=None, **options):
        result = self.model.column(column, option, **options)
        if options:
            self._schedule()
        return result

    def heading(self, column, option=None, **options):
        result = self.model.heading(column, option, **options)
        if options:
            self._schedule()
        return result

    def tag_configure(self, tagname, option=None, **options):
        result = self.model.tag_configure(tagname, option, **options)
        if options:
            self._tag_styles.setdefault(tagname, {}).update(options)
            self._schedule()
        return result

    def selection(self):
        return self.model.selection()

    def selection_set(self, *items):
        before = self.model.selection()
        self.model.selection_set(*items)
        self._schedule()
        if self.model.selection() != before:
            self.event_generate("<<TreeviewSelect>>", when="tail")

    def focus(self, item=None):
        return self.model.focus(item)

    def viewport(self):
        return max(1, self.winfo_height() - self.header_height)

    def _limit(self):
        return max(0, len(self._rows()) * self.row_height - self.viewport())

    def _resize(self, event):
        self.offset = min(self.offset, self._limit())
        self.target = min(self.target, self._limit())
        self._schedule()
        if self._pending_see is not None and self.winfo_height() > self.header_height + 1:
            item = self._pending_see
            self._pending_see = None
            if self.exists(item):
                self.see(item)

    def _cancel_animation(self):
        if self.animation is not None:
            self._timer_owner.after_cancel(self.animation)
            self.animation = None

    def _move(self, value, animate=False):
        self.target = max(0.0, min(self._limit(), float(value)))
        if animate:
            if self.animation is None:
                self.animation = self._timer_owner.after(12, self._tick)
        else:
            self._cancel_animation()
            self.offset = self.target
            self._schedule()

    def _tick(self):
        self.animation = None
        distance = self.target - self.offset
        self.offset = self.target if abs(distance) < 0.3 else self.offset + distance * 0.42
        self._schedule()
        if self.offset != self.target:
            self.animation = self._timer_owner.after(12, self._tick)

    def _wheel_steps(self, steps):
        self._move(self.target + steps * max(18 * self.scale, self.row_height * 0.55), True)
        return "break"

    def wheel(self, event):
        return self._wheel_steps(-float(event.delta) / 120)

    def yview(self, *args):
        total = max(1, len(self._rows()) * self.row_height)
        if not args:
            return min(1, self.offset / total), min(1, (self.offset + self.viewport()) / total)
        if args[0] == "moveto":
            self._move(float(args[1]) * total)
        elif args[0] == "scroll":
            amount = float(args[1])
            step = (
                self.viewport() * 0.9
                if args[2] == "pages"
                else max(18 * self.scale, self.row_height * 0.55)
            )
            self._move(self.offset + amount * step)

    def yview_moveto(self, fraction):
        self.yview("moveto", fraction)

    def yview_scroll(self, number, what):
        self.yview("scroll", number, what)

    def see(self, item):
        if self.winfo_height() <= self.header_height + 1:
            self._pending_see = item
            return
        parent = self.model.parent(item)
        while parent:
            self.model.item(parent, open=True)
            parent = self.model.parent(parent)
        self._visible = None
        self._rows()
        index = self._indices.get(item)
        if index is None:
            return
        top = index * self.row_height
        bottom = top + self.row_height
        if top < self.offset:
            self._move(top)
        elif bottom > self.offset + self.viewport():
            self._move(bottom - self.viewport())

    def bbox(self, item, column=None):
        self._rows()
        index = self._indices.get(item)
        if index is None:
            return ""
        y = self.header_height + index * self.row_height - self.offset
        if y + self.row_height <= self.header_height or y >= self.winfo_height():
            return ""
        return (0, round(y), self.winfo_width(), self.row_height)

    def identify_row(self, y):
        if y < self.header_height:
            return ""
        rows = self._rows()
        index = math.floor((y - self.header_height + self.offset) / self.row_height)
        return rows[index][0] if 0 <= index < len(rows) else ""

    def _click(self, event):
        self.focus_set()
        item = self.identify_row(event.y)
        if not item:
            return
        self.focus(item)
        self.selection_set(item)
        if self.model.get_children(item):
            self.item(item, open=not self.model.item(item, "open"))

    def _navigate(self, delta):
        rows = self._rows()
        if not rows:
            return "break"
        index = self._indices.get(self.focus(), -1)
        index = (
            0
            if delta == "home"
            else len(rows) - 1 if delta == "end" else max(0, min(len(rows) - 1, index + delta))
        )
        item = rows[index][0]
        self.focus(item)
        self.selection_set(item)
        self.see(item)
        return "break"

    def _branch(self, opened):
        item = self.focus()
        if not item:
            return "break"
        if self.get_children(item):
            self.item(item, open=opened)
        elif not opened:
            parent = self.parent(item)
            if parent:
                self.focus(parent)
                self.selection_set(parent)
                self.see(parent)
        return "break"

    def _activate(self):
        item = self.focus()
        if item and self.get_children(item):
            self.item(item, open=not self.item(item, "open"))
        return "break"

    def _fit_text(self, text, width, bold=False, small=False):
        face = self.small if small else self.bold if bold else self.face
        if face.measure(text) <= width:
            return text
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if face.measure(text[:middle] + "…") <= width:
                low = middle
            else:
                high = middle - 1
        return text[:low] + "…" if width > face.measure("…") else ""

    def _draw(self):
        self.redraw = None
        tk.Canvas.delete(self, "all")
        rows = self._rows()
        self.offset = min(self.offset, self._limit())
        self.target = min(self.target, self._limit())
        width = self.winfo_width()
        height = self.winfo_height()
        selected = set(self.selection())
        first = max(0, int(self.offset / self.row_height))
        last = min(len(rows), math.ceil((self.offset + self.viewport()) / self.row_height) + 1)
        for index in range(first, last):
            item, depth = rows[index]
            data = self.model.item(item)
            top = self.header_height + index * self.row_height - self.offset
            active = item in selected
            if active:
                self.create_rectangle(
                    0, top, width, top + self.row_height, fill="#EBF2FC", outline=""
                )
            color = "#235DAB" if active else "#243247"
            if self.books:
                lines = data["text"].split("\n", 1)
                title = lines[0].lstrip("☑☐ ").strip()
                detail = lines[1].strip() if len(lines) > 1 else ""
                x = 13 * self.scale
                y = top + 41 * self.scale
                box = 11 * self.scale
                checked = data["text"].startswith("☑")
                self.create_rectangle(
                    x,
                    y - box / 2,
                    x + box,
                    y + box / 2,
                    fill="#3679D5" if checked else "#FFFFFF",
                    outline="#3679D5" if checked else "#9AA7B7",
                    width=1,
                )
                if checked:
                    self.create_line(
                        x + 2 * self.scale,
                        y,
                        x + 4.5 * self.scale,
                        y + 3 * self.scale,
                        x + 9 * self.scale,
                        y - 3 * self.scale,
                        fill="#FFFFFF",
                        width=max(1, 1.3 * self.scale),
                    )
                left = 80 * self.scale
                available = max(0, int(width - left - 10 * self.scale))
                if data.get("image"):
                    self.create_image(
                        33 * self.scale,
                        top + 41 * self.scale,
                        image=data["image"][0],
                        anchor="w",
                        tags=("book-cover",),
                    )
                self.create_text(
                    left,
                    top + 19 * self.scale,
                    text=self._fit(title, available),
                    font=self.face,
                    fill=color,
                    anchor="w",
                )
                self.create_text(
                    left,
                    top + 41 * self.scale,
                    text=self._fit(detail, available, small=True),
                    font=self.small,
                    fill="#567096" if active else "#7A8595",
                    anchor="w",
                )
                values = data["values"]
                badge = str(values[5]) if len(values) > 5 else ""
                badge_color = (
                    "#A56830"
                    if "失败" in badge or "待补" in badge or "待核对" in badge
                    else "#397A70" if "匹配" in badge or "已有" in badge else "#8A97A6"
                )
                self.create_text(
                    left,
                    top + 62 * self.scale,
                    text=self._fit(badge, available, small=True),
                    font=self.small,
                    fill=badge_color,
                    anchor="w",
                    tags=("cover-state",),
                )
            else:
                bold = bool(set(data["tags"]) & {"full", "volume"})
                if not active and bold:
                    color = "#285EAA" if "full" in data["tags"] else "#465A74"
                x = (9 + depth * 15) * self.scale
                middle = top + self.row_height / 2
                if self.get_children(item):
                    r = 3 * self.scale
                    points = (
                        (x - r, middle - r / 2, x + r, middle - r / 2, x, middle + r)
                        if data["open"]
                        else (x - r / 2, middle - r, x - r / 2, middle + r, x + r, middle)
                    )
                    self.create_polygon(points, fill="#8493A8", outline="")
                left = x + 10 * self.scale
                tail = 52 * self.scale
                self.create_text(
                    left,
                    middle,
                    text=self._fit(
                        data["text"], max(0, int(width - left - tail - 10 * self.scale)), bold
                    ),
                    font=self.bold if bold else self.face,
                    fill=color,
                    anchor="w",
                )
                values = data["values"]
                if values:
                    self.create_text(
                        width - 12 * self.scale,
                        middle,
                        text=str(values[0]),
                        font=self.small,
                        fill="#7190B5" if active else "#8A97A9",
                        anchor="e",
                    )
        # Header overlays partially scrolled rows, so the top row can be clipped smoothly.
        if self.headers:
            self.create_rectangle(0, 0, width, self.header_height, fill="#F7F8FA", outline="")
            self.create_text(
                8 * self.scale,
                self.header_height / 2,
                text=self.model.heading("#0", "text"),
                font=self.small,
                fill="#788599",
                anchor="w",
            )
            columns = self.model["columns"]
            if columns:
                self.create_text(
                    width - 12 * self.scale,
                    self.header_height / 2,
                    text=self.model.heading(columns[0], "text"),
                    font=self.small,
                    fill="#788599",
                    anchor="e",
                )
        if self._scroll_command:
            self._scroll_command(*self.yview())
