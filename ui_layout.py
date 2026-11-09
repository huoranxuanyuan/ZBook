# SPDX-License-Identifier: AGPL-3.0-only
"""ZBook workspace: reading on the left, chapters and book queue on the right."""

import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
from branding import APP_NAME, VERSION, application_icon
from ui_widgets import SoftButton, SlimScrollbar, NativeFileDrop
from smooth_tree import PixelTreeview
from layout_widgets import (
    Surface,
    SegmentedControl,
    Tooltip,
    ProgressMeter,
    FormatSwitch,
    QuietPanedWindow,
)

BG = "#F4F5F7"
INK = "#243247"
MUTED = "#7A8595"


def build_ui(self):
    root = self.root
    scale = float(root.tk.call("tk", "scaling")) / (96 / 72)
    self.ui_scale = scale
    root.title(f"{APP_NAME} {VERSION}")
    self.branding_icons = [
        ImageTk.PhotoImage(
            application_icon().resize((size, size), Image.Resampling.LANCZOS), master=root
        )
        for size in (16, 32, 48, 64)
    ]
    root.iconphoto(True, *self.branding_icons)
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    width = min(int(1260 * scale), screen_w - 72)
    height = min(int(850 * scale), screen_h - 90)
    self.compact = height / scale < 740
    self.cover_preview_size = (
        int((100 if self.compact else 112) * scale),
        int((150 if self.compact else 168) * scale),
    )
    root.geometry(f"{width}x{height}+36+24")
    root.minsize(min(int(1020 * scale), screen_w - 72), min(int(720 * scale), screen_h - 90))
    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=("Microsoft YaHei UI", 10), background="#FFFFFF", foreground=INK)
    style.configure("TFrame", background="#FFFFFF")
    style.configure("TLabel", background="#FFFFFF")
    style.configure(
        "TEntry",
        padding=(9, 7),
        fieldbackground="#F6F7F9",
        bordercolor="#E8EBF0",
        lightcolor="#E8EBF0",
        darkcolor="#E8EBF0",
    )
    style.configure(
        "TCombobox",
        padding=(8, 5),
        fieldbackground="#F6F7F9",
        background="#F6F7F9",
        bordercolor="#E8EBF0",
        arrowcolor="#75839A",
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", "#F6F7F9")],
        foreground=[("readonly", INK)],
        selectbackground=[("readonly", "#F6F7F9")],
        selectforeground=[("readonly", INK)],
    )
    style.configure(
        "Treeview",
        rowheight=int(34 * scale),
        background="#FFFFFF",
        fieldbackground="#FFFFFF",
        borderwidth=0,
        font=("Microsoft YaHei UI", 10),
    )
    style.configure(
        "Books.Treeview",
        rowheight=int(70 * scale),
        background="#FFFFFF",
        fieldbackground="#FFFFFF",
        font=("Microsoft YaHei UI", 10),
    )
    style.map(
        "Treeview", background=[("selected", "#EBF2FC")], foreground=[("selected", "#235DAB")]
    )
    style.configure(
        "Treeview.Heading",
        background="#F7F8FA",
        foreground="#788599",
        font=("Microsoft YaHei UI", 9),
        padding=(8, 7),
        borderwidth=0,
        relief="flat",
    )
    style.layout(
        "Treeview",
        [
            (
                "Treeview.field",
                {
                    "sticky": "nswe",
                    "children": [
                        (
                            "Treeview.padding",
                            {
                                "sticky": "nswe",
                                "children": [("Treeview.treearea", {"sticky": "nswe"})],
                            },
                        )
                    ],
                },
            )
        ],
    )
    style.configure(
        "TProgressbar",
        background="#3679D5",
        troughcolor="#EDF1F7",
        borderwidth=0,
        lightcolor="#3679D5",
        darkcolor="#3679D5",
    )

    header = tk.Frame(root, bg=BG)
    header.pack(
        fill="x",
        padx=int(24 * scale),
        pady=(int((10 if self.compact else 16) * scale), int((10 if self.compact else 14) * scale)),
    )
    tk.Label(
        header, text="ZBook", bg=BG, fg=INK, font=("Segoe UI", 20 if self.compact else 22, "bold")
    ).pack(side="left")
    tk.Label(header, text="小说转换器", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 10)).pack(
        side="left", padx=12, pady=(8, 0)
    )
    actions = tk.Frame(header, bg=BG)
    actions.pack(side="right")
    self.import_files_button = self.button(
        actions,
        "导入书籍",
        self.add_files,
        pack={"side": "right", "padx": (int(8 * scale), 0)},
        primary=True,
    )
    self.import_folder_button = self.button(
        actions, "导入文件夹", self.add_folder, pack={"side": "right"}
    )
    self.settings_button = self.button(
        actions, "设置", self.show_settings, pack={"side": "right", "padx": (0, int(8 * scale))}
    )

    footer = tk.Frame(root, bg="#FFFFFF", highlightbackground="#E5E9EF", highlightthickness=1)
    footer.pack(side="bottom", fill="x")
    destination = tk.Frame(footer, bg="#FFFFFF")
    destination.pack(fill="x", padx=int(24 * scale), pady=(int(11 * scale), int(7 * scale)))
    self.output = tk.StringVar(value=self.settings["output"])
    self.converter = tk.StringVar(value=self.settings["converter"])
    self.epub = tk.BooleanVar(value=self.settings["epub"])
    self.mobi = tk.BooleanVar(value=self.settings["mobi"])
    self.auto_cover = tk.BooleanVar(value=self.settings["auto_cover"])
    if not self.epub.get() and not self.mobi.get():
        self.mobi.set(True)
    self.line_height = tk.StringVar(value=self.settings["line_height"])
    self.gap = tk.StringVar(value=self.settings["paragraph_gap"])
    tk.Label(
        destination, text="保存到", bg="#FFFFFF", fg=MUTED, font=("Microsoft YaHei UI", 9)
    ).pack(side="left")
    entry = ttk.Entry(destination, textvariable=self.output, width=1)
    entry.pack(side="left", fill="x", expand=True, padx=int(12 * scale))
    self.controls.append(entry)
    self.button(destination, "更改", self.choose_output)
    self.button(destination, "打开文件夹", self.open_output, managed=False)
    lower = tk.Frame(footer, bg="#FFFFFF")
    lower.pack(fill="x", padx=int(24 * scale), pady=(0, int(12 * scale)))
    self.format_choice = tk.StringVar(
        value=(
            "EPUB + MOBI"
            if self.epub.get() and self.mobi.get()
            else "EPUB" if self.epub.get() else "MOBI"
        )
    )
    self.format_control = FormatSwitch(
        lower, variable=self.format_choice, values=("MOBI", "EPUB", "EPUB + MOBI"), scale=scale
    )
    self.format_control.grid(row=0, column=0, sticky="w")
    self.controls.append(self.format_control)
    self.format_control.bind(
        "<<FormatChanged>>",
        lambda event: (
            self.epub.set("EPUB" in self.format_choice.get()),
            self.mobi.set("MOBI" in self.format_choice.get()),
            self.persist(),
        ),
    )
    progress_box = tk.Frame(lower, bg="#FFFFFF")
    progress_box.grid(row=0, column=1, padx=int(12 * scale))
    progress_head = tk.Frame(progress_box, bg="#FFFFFF")
    progress_head.pack(fill="x", pady=(0, int(4 * scale)))
    self.progress_phase = tk.StringVar(value="待转换")
    self.progress_percent = tk.StringVar(value="0%")
    tk.Label(
        progress_head,
        textvariable=self.progress_phase,
        bg="#FFFFFF",
        fg=MUTED,
        font=("Microsoft YaHei UI", 8),
        anchor="w",
    ).pack(side="left")
    tk.Label(
        progress_head,
        textvariable=self.progress_percent,
        bg="#FFFFFF",
        fg="#9AA4B2",
        font=("Microsoft YaHei UI", 8),
        anchor="e",
    ).pack(side="right")
    self.progress = ProgressMeter(progress_box, width=112, scale=scale)
    self.progress.pack(fill="x")
    self.status = tk.StringVar(value="从右上角导入作品，或拖入右侧书籍列表")
    lower.columnconfigure(2, weight=1)
    tk.Label(
        lower,
        textvariable=self.status,
        bg="#FFFFFF",
        fg=MUTED,
        font=("Microsoft YaHei UI", 9),
        width=1,
        anchor="w",
    ).grid(row=0, column=2, sticky="ew")
    self.stop_button = SoftButton(lower, text="停止", command=self.stop, width=62)
    self.stop_button.grid(row=0, column=3, padx=int(10 * scale))
    self.stop_button.state(["disabled"])
    self.start_button = SoftButton(
        lower, text="开始转换", command=self.start, primary=True, width=150, height=38
    )
    self.start_button.grid(row=0, column=4)

    main = tk.Frame(root, bg=BG)
    main.pack(fill="both", expand=True, padx=int(24 * scale), pady=(0, int(18 * scale)))
    main.rowconfigure(0, weight=1)
    main.columnconfigure(0, weight=1)
    self.sidebar_surface = Surface(
        main, scale=scale, padding=16, expand=True, width=int(288 * scale)
    )
    self.sidebar_surface.grid(row=0, column=1, sticky="nsew", padx=(int(18 * scale), 0))
    self.sidebar_surface.grid_propagate(False)
    sidebar = self.sidebar_surface.body
    self.sidebar = sidebar
    sidebar.columnconfigure(0, weight=1)
    sidebar.rowconfigure(2, weight=1)
    heading = tk.Frame(sidebar, bg="#FFFFFF")
    heading.grid(row=0, column=0, sticky="ew", pady=(0, int(12 * scale)))
    tk.Label(
        heading, text="书籍列表", bg="#FFFFFF", fg=INK, font=("Microsoft YaHei UI", 11, "bold")
    ).pack(side="left")
    self.summary = tk.StringVar(value="0 本")
    tk.Label(
        heading, textvariable=self.summary, bg="#FFFFFF", fg=MUTED, font=("Microsoft YaHei UI", 9)
    ).pack(side="right")
    batch_actions = tk.Frame(sidebar, bg="#FFFFFF")
    batch_actions.grid(row=3, column=0, sticky="ew", pady=(int(10 * scale), 0))
    for index in range(2):
        batch_actions.columnconfigure(index, weight=1, uniform="batch-actions")
    self.organize_all_button = SoftButton(
        batch_actions, text="重新识别目录", command=self.organize_all, width=120, height=34
    )
    self.organize_all_button.grid(row=0, column=0, sticky="ew", padx=(0, int(6 * scale)))
    self.prepare_all_button = SoftButton(
        batch_actions,
        text="一键搜索封面",
        command=self.prepare_all,
        width=120,
        height=34,
        primary=True,
    )
    self.prepare_all_button.grid(row=0, column=1, sticky="ew")
    self.controls.extend([self.organize_all_button, self.prepare_all_button])
    self.prepare_all_tip = Tooltip(
        self.prepare_all_button,
        "联网搜索并应用列表内全部书籍的缺失图片封面；已有图片保留，不重新识别目录",
    )
    self.organize_all_tip = Tooltip(
        self.organize_all_button,
        "导入时已自动识别目录；原文件或章节规则修改后，可重新识别全部书籍，不搜索封面",
    )
    queuebox = tk.Frame(sidebar, bg="#FFFFFF")
    queuebox.grid(row=2, column=0, sticky="nsew")
    queuebox.rowconfigure(0, weight=1)
    queuebox.columnconfigure(0, weight=1)
    cols = ("check", "title", "author", "chapters", "status", "cover")
    self.tree = PixelTreeview(
        queuebox,
        columns=cols,
        displaycolumns=(),
        show="tree",
        selectmode="browse",
        style="Books.Treeview",
        height=1,
    )
    self.tree.column("#0", width=int(240 * scale), minwidth=40, stretch=True)
    self.tree.grid(row=0, column=0, sticky="nsew")
    self.queue_scroll = SlimScrollbar(queuebox, command=self.tree.yview)
    self.queue_scroll.grid(row=0, column=1, sticky="ns")
    self.tree.configure(yscrollcommand=self.queue_scroll.set)
    self.tree.bind("<Double-Button-1>", self.on_queue_double_click)
    self.tree.bind("<<TreeviewSelect>>", self.on_select)
    self.tree.bind("<Button-1>", self.on_queue_click)
    self.tree.bind("<Button-3>", self.queue_menu)
    self.empty_queue = tk.Label(
        queuebox,
        text="将书籍拖到这里\n或从右上角导入\n\nTXT · Word · PDF\n每次最多导入 50 个文件",
        bg="#FFFFFF",
        fg="#9AA4B2",
        font=("Microsoft YaHei UI", 9),
        justify="center",
    )
    self.empty_queue.place(relx=0.5, rely=0.44, anchor="center")
    selection = tk.Frame(sidebar, bg="#FFFFFF")
    selection.grid(row=1, column=0, sticky="ew", pady=(0, int(10 * scale)))
    for index in range(3):
        selection.columnconfigure(index, weight=1, uniform="queue-actions")
    self.select_all_button = SoftButton(
        selection, text="全选", command=self.select_all, width=64, height=32
    )
    self.select_none_button = SoftButton(
        selection, text="取消全选", command=self.select_none, width=84, height=32
    )
    self.remove_selected_button = SoftButton(
        selection, text="移除选中", command=self.remove_selected, width=84, height=32
    )
    for index, button in enumerate(
        (self.select_all_button, self.select_none_button, self.remove_selected_button)
    ):
        button.grid(row=0, column=index, sticky="ew", padx=(0, int(5 * scale)) if index < 2 else 0)
        self.controls.append(button)
    self.queue_notice = tk.StringVar(value="导入自动分章 · 封面可单独搜索")
    self.queue_notice_label = tk.Label(
        sidebar,
        textvariable=self.queue_notice,
        bg="#FFFFFF",
        fg=MUTED,
        font=("Microsoft YaHei UI", 9),
        wraplength=int(250 * scale),
        justify="left",
    )
    self.queue_notice_label.grid(row=4, column=0, sticky="ew", pady=(int(10 * scale), 0))

    detail = tk.Frame(main, bg=BG)
    detail.grid(row=0, column=0, sticky="nsew")
    detail.columnconfigure(0, weight=1)
    detail.rowconfigure(1, weight=1)
    self.metadata_surface = Surface(
        detail, scale=scale, padding=12 if self.compact else 16, height=180
    )
    self.metadata_surface.grid(row=0, column=0, sticky="ew", pady=(0, int(14 * scale)))
    top = self.metadata_surface.body
    top.columnconfigure(1, weight=1)
    coverbox = tk.Frame(top, bg="#FFFFFF")
    coverbox.grid(row=0, column=0, sticky="n", padx=(0, int(20 * scale)))
    self.cover_label = tk.Label(coverbox, bg="#FFFFFF", bd=0)
    self.cover_label.pack()
    info = tk.Frame(top, bg="#FFFFFF")
    info.grid(row=0, column=1, sticky="nsew")
    info.columnconfigure(0, weight=1)
    self.title = tk.StringVar()
    self.author = tk.StringVar()
    self.encoding = tk.StringVar(value="自动")
    self.rule = tk.StringVar(value="自动（中文 / 英文）")
    self.regex = tk.StringVar()
    fields = tk.Frame(info, bg="#FFFFFF")
    fields.grid(row=0, column=0, sticky="ew")
    for index, (name, variable, face) in enumerate(
        (
            ("书名", self.title, ("Microsoft YaHei UI", 16, "bold")),
            ("作者", self.author, ("Microsoft YaHei UI", 12)),
        )
    ):
        fields.columnconfigure(index, weight=3 if index == 0 else 2)
        field = tk.Frame(fields, bg="#FFFFFF")
        field.grid(row=0, column=index, sticky="ew", padx=(0, int(12 * scale)) if index == 0 else 0)
        tk.Label(field, text=name, bg="#FFFFFF", fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(
            anchor="w"
        )
        entry = ttk.Entry(field, textvariable=variable, font=face, width=1)
        entry.pack(fill="x", pady=(int(4 * scale), int(6 * scale)))
        self.controls.append(entry)
    source_row = tk.Frame(info, bg="#FFFFFF")
    source_row.grid(row=1, column=0, sticky="ew", pady=(0, int(8 * scale)))
    source_row.columnconfigure(1, weight=1)
    tk.Label(
        source_row, text="源文件", bg="#FFFFFF", fg=MUTED, font=("Microsoft YaHei UI", 9)
    ).grid(row=0, column=0, sticky="w", padx=(0, int(8 * scale)))
    self.source_name = tk.StringVar(value="尚未导入文件")
    self.source_path = ""
    self.source_label = tk.Label(
        source_row,
        textvariable=self.source_name,
        bg="#FFFFFF",
        fg="#607086",
        font=("Microsoft YaHei UI", 9),
        width=1,
        anchor="w",
    )
    self.source_label.grid(row=0, column=1, sticky="ew")
    self.source_tip = Tooltip(self.source_label, lambda: self.source_path or "原文件名与位置")
    self.save_metadata_button = SoftButton(
        source_row, text="保存信息", command=self.apply_current, height=30
    )
    self.save_metadata_button.grid(row=0, column=2, padx=(int(8 * scale), 0))
    self.controls.append(self.save_metadata_button)
    action = tk.Frame(info, bg="#FFFFFF")
    action.grid(row=2, column=0, sticky="ew")
    self.search_cover_button = SoftButton(
        action, text="搜索封面", command=self.online_cover, height=32
    )
    self.import_cover_button = SoftButton(
        action, text="本地封面", command=self.import_cover, height=32
    )
    self.paste_cover_button = SoftButton(
        action, text="粘贴封面", command=self.paste_cover, height=32
    )
    self.text_cover_button = SoftButton(
        action, text="生成封面", command=self.clear_cover, height=32
    )
    for control in (
        self.search_cover_button,
        self.import_cover_button,
        self.paste_cover_button,
        self.text_cover_button,
    ):
        control.pack(side="left", padx=(0, int(5 * scale)))
        self.controls.append(control)
    self.paste_cover_tip = Tooltip(self.paste_cover_button, "从剪贴板粘贴图片")
    self.notes = tk.StringVar(value="导入作品后，自动识别书名、作者与章节。")
    self.notes_label = tk.Label(
        info,
        textvariable=self.notes,
        bg="#FFFFFF",
        fg=MUTED,
        font=("Microsoft YaHei UI", 9),
        anchor="w",
        justify="left",
        height=1,
        wraplength=int(460 * scale),
    )
    self.notes_label.grid(row=3, column=0, sticky="ew", pady=(int(9 * scale), 0))
    self.notes_tip = Tooltip(self.notes_label, self.notes.get)
    self.cover_info = tk.StringVar(value="")
    self.cover_info_label = tk.Label(
        info,
        textvariable=self.cover_info,
        bg="#FFFFFF",
        fg="#8A95A6",
        font=("Microsoft YaHei UI", 9),
        anchor="w",
        justify="left",
        height=1,
        wraplength=int(460 * scale),
    )
    self.cover_info_label.grid(row=4, column=0, sticky="ew", pady=(int(3 * scale), 0))
    self.cover_detail_tip = Tooltip(self.cover_info_label, self.cover_info.get)
    info.bind(
        "<Configure>",
        lambda event: (
            self.notes_label.configure(wraplength=max(80, event.width)),
            self.cover_info_label.configure(wraplength=max(80, event.width)),
        ),
    )

    self.preview_surface = Surface(detail, scale=scale, padding=16, expand=True)
    self.preview_surface.grid(row=1, column=0, sticky="nsew")
    workspace = self.preview_surface.body
    workspace.columnconfigure(0, weight=1)
    workspace.rowconfigure(1, weight=1)
    modebar = tk.Frame(workspace, bg="#FFFFFF")
    modebar.grid(row=0, column=0, sticky="ew", pady=(0, int(12 * scale)))
    pages = tk.Frame(workspace, bg="#FFFFFF")
    pages.grid(row=1, column=0, sticky="nsew")
    pages.columnconfigure(0, weight=1)
    pages.rowconfigure(0, weight=1)
    preview = tk.Frame(pages, bg="#FFFFFF")
    logs = tk.Frame(pages, bg="#FFFFFF")
    for page in (preview, logs):
        page.grid(row=0, column=0, sticky="nsew")
    self.workspace_tabs = SegmentedControl(
        modebar,
        ("章节目录", "转换记录"),
        lambda index: (preview if index == 0 else logs).tkraise(),
        scale,
    )
    self.workspace_tabs.pack(side="left")
    preview.tkraise()
    self.preview_title = tk.StringVar(value="请选择章节")
    self.preview_info = tk.StringVar(value="导入后自动识别分卷与章节")
    toolbar = tk.Frame(preview, bg="#FFFFFF")
    toolbar.columnconfigure(0, weight=1)
    toolbar.pack(fill="x", pady=(0, int(10 * scale)))
    self.preview_caption = tk.Label(
        toolbar,
        textvariable=self.preview_info,
        bg="#FFFFFF",
        fg=MUTED,
        font=("Microsoft YaHei UI", 9),
        width=1,
        anchor="w",
    )
    self.preview_caption.grid(row=0, column=0, sticky="ew")
    chapter_actions = tk.Frame(toolbar, bg="#FFFFFF")
    chapter_actions.grid(row=0, column=1, sticky="e")
    self.outline_buttons = [
        self.button(chapter_actions, "章节改名", self.rename_chapter),
        self.button(chapter_actions, "重新识别", self.reset_chapters),
    ]
    self.preview_split = QuietPanedWindow(preview, scale=scale)
    self.preview_split.pack(fill="both", expand=True)
    cf = tk.Frame(self.preview_split, bg="#FFFFFF")
    bf = tk.Frame(self.preview_split, bg="#FFFFFF")
    self.preview_split.add(cf, minsize=int(300 * scale), stretch="always")
    self.preview_split.add(bf, minsize=int(195 * scale), stretch="always")
    self.catalog_pane = cf
    self.body_pane = bf
    self.preview_ratio = 0.65
    self.preview_resize_job = None
    self.preview_split.bind("<Configure>", self.schedule_preview_resize, add="+")
    self.preview_split.bind("<ButtonRelease-1>", self.remember_preview_ratio, add="+")
    self.preview_split.bind("<<SashMoved>>", self.remember_preview_ratio)
    cf.columnconfigure(0, weight=1)
    cf.rowconfigure(0, weight=1)
    bf.columnconfigure(0, weight=1)
    bf.rowconfigure(2, weight=1)
    self.chapter_tree = PixelTreeview(
        cf, columns=("line",), show="tree headings", selectmode="browse", height=1
    )
    self.chapter_tree.heading("#0", text="章节与分卷")
    self.chapter_tree.heading("line", text="行号")
    self.chapter_tree.column("#0", width=int(390 * scale), minwidth=80, stretch=True)
    self.chapter_tree.column(
        "line", width=int(50 * scale), minwidth=30, stretch=False, anchor="center"
    )
    self.chapter_tree.tag_configure(
        "full", foreground="#285EAA", font=("Microsoft YaHei UI", 10, "bold")
    )
    self.chapter_tree.tag_configure(
        "volume", foreground="#465A74", font=("Microsoft YaHei UI", 10, "bold")
    )
    self.chapter_tree.bind("<Button-3>", self.outline_menu)
    self.chapter_scroll = SlimScrollbar(cf, command=self.chapter_tree.yview)
    self.chapter_tree.configure(yscrollcommand=self.chapter_scroll.set)
    self.chapter_tree.grid(row=0, column=0, sticky="nsew")
    self.chapter_scroll.grid(row=0, column=1, sticky="ns")
    self.chapter_tree.bind("<<TreeviewSelect>>", self.show_chapter)
    tk.Label(
        bf,
        text="正文预览",
        bg="#FFFFFF",
        fg="#8794A6",
        font=("Microsoft YaHei UI", 9),
        anchor="w",
        padx=int(10 * scale),
        pady=int(5 * scale),
    ).grid(row=0, column=0, columnspan=2, sticky="ew")
    self.preview_header = tk.Label(
        bf,
        textvariable=self.preview_title,
        bg="#FFFFFF",
        fg="#56657A",
        font=("Microsoft YaHei UI", 10, "bold"),
        anchor="w",
        padx=int(10 * scale),
        pady=int(6 * scale),
        width=1,
    )
    self.preview_header.grid(row=1, column=0, columnspan=2, sticky="ew")
    bf.bind(
        "<Configure>",
        lambda event: self.preview_header.configure(
            wraplength=max(50, event.width - int(16 * scale))
        ),
    )
    self.body = tk.Text(
        bf,
        width=1,
        height=1,
        wrap="word",
        bg="#FFFFFF",
        fg="#7C8899",
        font=("Microsoft YaHei UI", 10),
        relief="flat",
        bd=0,
        highlightthickness=0,
        padx=int(10 * scale),
        pady=int(8 * scale),
        spacing3=int(5 * scale),
        state="disabled",
    )
    self.body_scroll = SlimScrollbar(bf, command=self.body.yview)
    self.body.configure(yscrollcommand=self.body_scroll.set)
    self.body.grid(row=2, column=0, sticky="nsew")
    self.body_scroll.grid(row=2, column=1, sticky="ns")
    log_tools = tk.Frame(logs, bg="#FFFFFF")
    log_tools.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, int(8 * scale)))
    tk.Label(
        log_tools, text="本次转换进度", bg="#FFFFFF", fg=MUTED, font=("Microsoft YaHei UI", 9)
    ).pack(side="left")
    self.button(log_tools, "打开日志", self.open_report, pack={"side": "right"}, managed=False)
    logs.columnconfigure(0, weight=1)
    logs.rowconfigure(1, weight=1)
    self.logs = tk.Text(
        logs,
        width=1,
        height=1,
        wrap="word",
        bg="#FFFFFF",
        fg="#647286",
        font=("Microsoft YaHei UI", 10),
        relief="flat",
        bd=0,
        highlightthickness=0,
        state="disabled",
    )
    self.log_scroll = SlimScrollbar(logs, command=self.logs.yview)
    self.logs.configure(yscrollcommand=self.log_scroll.set)
    self.logs.grid(row=1, column=0, sticky="nsew")
    self.log_scroll.grid(row=1, column=1, sticky="ns")
    self.placeholder_cover()
    root.bind("<Control-o>", lambda event: self.add_files())
    root.bind("<Control-Return>", lambda event: self.start())
    root.bind("<F5>", lambda event: self.apply_current())
    self.file_drop = NativeFileDrop(
        root,
        [self.sidebar_surface, sidebar, self.tree, self.empty_queue],
        self.add_paths,
        on_limit=self.reject_import,
    )
