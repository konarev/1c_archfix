import collections
import functools
import inspect
import itertools
import os
import re
import shutil
import subprocess
import sys
import types
import typing as t
from dataclasses import dataclass, field
from pathlib import Path

# from qtpy.QtCore import Qt
# pylint: disable= E0611
from qtpy import QtCore, QtWidgets

# from PyQt5.QtCore import QIODevice
from qtpy.QtCore import (
    QAbstractListModel,
    QFile,
    QIODevice,
    QModelIndex,
    QObject,
    QSignalBlocker,
    QStrngLi,
    Qt,
    Signal,
)
from qtpy.QtGui import QBitmap, QBrush, QColor, QIcon, QStandardItem, QStandardItemModel
from qtpy.QtWidgets import (
    QApplication,
    QDataWidgetMapper,
    QErrorMessage,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QWidget,
)
from qtpy.uic import loadUi

from py_utils import utils


def calc_file_hash(file: Path) -> int:
    if file and file.exists():
        stat = file.stat()
        return hash((stat.st_size, stat.st_mtime))
    return 0


@dataclass(unsafe_hash=True)
class DesktopFile:
    filename: str
    preload_libstdc: bool | None = field(default=True, hash=True)
    set_scale: bool = field(default=False, hash=True)
    scale: int | None = field(default=1, hash=True)
    set_dpi_scale: bool = field(default=False, hash=True)
    dpi_scale: float | None = field(default=1.0, hash=True)
    command: str = field(default="", hash=True)
    name_ru: str = field(default="", hash=True)
    name_en: str = field(default="", hash=True)
    readonly: bool = field(default=False, hash=False, init=False)

    _pattern = re.compile(
        pattern=r"^\[Desktop"
        r" Entry\](?:.|\n)*^Exec=(?P<exec>(?P<preload_libstdc>LD_PRELOAD=/usr/lib/libstdc\+\+\.so\.6)?"
        r"\s*(?:GDK_SCALE=(?P<scale>\d+))?(?:\s*GDK_DPI_SCALE=(?P<dpi_scale>\d+(?:\.\d+)?))?"
        r"\s*(?P<command>.*))$(?:.|\n)*(?:^Name\[ru_RU\]=(?P<name_ru>.*))$"
        r"(?:.|\n)*(?:^Name=(?P<name_en>.*))$",
        flags=re.MULTILINE,
    )

    _last_save_obj_hash = -1
    _last_set_content_hash = -1
    _last_parse_content_hash = -1
    _last_file_hash = -1

    def __post_init__(self):
        self._last_set_content_hash = hash(self)
        self.update()

    _content = ""

    # @property
    def update(self):
        def _parse_content():
            content = self._content
            match = self._pattern.search(string=content)
            self.scale = int(float((scale := match["scale"]) or 1))
            self.dpi_scale = float((dpi_scale := match["dpi_scale"]) or 1.0)
            self.set_dpi_scale = dpi_scale is not None
            self.set_scale = scale is not None
            self.preload_libstdc = bool(match["preload_libstdc"])
            self.command = match["command"]
            self.name_ru = match["name_ru"]
            self.name_en = match["name_en"]
            self._last_parse_content_hash = hash(self._content)

        def _set_content():
            def gen_template(_match: re.Match, keywords: dict[str, str]) -> str:
                source = _match.string
                tmplt = ""
                start_pos = 0
                for name, value in _match.groupdict().items():
                    if not value or (name not in keywords):
                        continue
                    start, end = _match.span(name)
                    tmplt += source[start_pos:start] + keywords[name]
                    start_pos = end
                tmplt += source[start_pos:]
                return tmplt

            if _match := self._pattern.search(string=self._content):
                self._content = gen_template(
                    _match,
                    {"exec": self.exec_command, "name_ru": self.name_ru, "name_en": self.name_en},
                )
                self._last_set_content_hash = hash(self)

        if (
            self.filename
            and (path := Path(self.filename))
            and (file_hash := calc_file_hash(path)) != self._last_file_hash
        ):
            self._last_file_hash = file_hash
            self.readonly = not os.access(path, os.W_OK)
            with open(file=path, mode="rt", encoding="utf-8") as f:
                self.set_content(f.read())
        elif self._last_parse_content_hash != hash(self._content):
            _parse_content()
        elif self._last_set_content_hash != hash(self):
            _set_content()

    # @content.setter

    def get_content(self) -> str:
        self.update()
        return self._content

    def set_content(self, new_content: str):
        self._content = new_content
        self.update()

    @property
    def exec_command(self) -> str:
        return (
            ("LD_PRELOAD=/usr/lib/libstdc++.so.6 " if self.preload_libstdc else "")
            + (f"GDK_SCALE={self.scale} " if self.set_scale else "")
            + (f"GDK_DPI_SCALE={self.dpi_scale} " if self.set_dpi_scale else "")
            + f"{self.command}"
        )

    def save(self):
        if self._last_save_obj_hash == hash(self):
            # Объект не менялся после последней записи
            return
        with open(file=self.filename, mode="wt", encoding="utf-8") as f:
            f.write(self.get_content())
        self._last_save_obj_hash = hash(self)

    @classmethod
    def has_content(cls, filename: str) -> bool:
        with open(file=filename, mode="rt", encoding="utf-8") as f:
            return cls._pattern.search(string=f.read()) is not None


def find_desktop_files() -> list[DesktopFile]:
    files = []
    for path in (
        "/usr/share/applications",
        str(Path.home().absolute()) + "/.local/share/applications",
    ):
        files += [
            DesktopFile(str(file))
            for file in Path(path).iterdir()
            if file.is_file()
            and file.suffix == ".desktop"
            and file.name.startswith("1cestart")
            and DesktopFile.has_content(str(file))
        ]
    return files


# @dataclass
# class ExtField:
#     field: str
#     get_method: t.Callable | None = None
#     set_method: t.Callable | None = None


# T = t.TypeVar("T")


class PyClassModel(QStandardItemModel):
    def __init__(
        self,
        objects: list,
        # common_attrs: list[str | object],
        type_object: type,
        parent_widget: QWidget,
        member_with_get_set_callable_as_attr: bool = True,
        member_property_as_attr: bool = True,
        bind_widget_dict: t.Optional[dict[str, QWidget | set[QWidget]]] = None,
        /,
        **bind_widgets_kw: QWidget | set[QWidget],
    ) -> None:
        @functools.lru_cache
        def _attrs_of_class(obj: type):
            return utils.attrs_of_class(
                obj=obj,
                member_with_get_set_callable_as_attr=member_with_get_set_callable_as_attr,
                member_property_as_attr=member_property_as_attr,
            )

        def get_property(widget: QWidget, name: str) -> t.Any | None:
            if not hasattr(widget, name):
                return None
            return getattr(widget, name)

        def bind_widget(
            storage: dict[str, QWidget | set[QWidget]], widget: QWidget, attr_name: str
        ):
            if prev := storage.get(attr_name, None):
                prev2 = set(prev) | set(widget)
            else:
                prev2 = set(widget)
            storage[attr_name] = prev2


        class SetWidget(t.NamedTuple):
            widgets: set[QWidget]
            attr_desc: utils.AttrsDesc

        self.objects = objects
        self.type_object = type_object
        attrs = _attrs_of_class(type_object)
        self.attrs: dict[str, SetWidget] = {}
        bind_widget_dict = bind_widget_dict or {}
        bind_widgets_kw = bind_widgets_kw or {}

        for widget in parent_widget.children():
            if not isinstance(widget, QWidget):
                continue
            if attr_name := get_property(widget, "field"):
                bind_widget(bind_widget_dict,widget,attr_name)
            elif attrs_name := t.cast(list[str],get_property(widget, "fields")):
                for attr_name in attrs_name:
                    bind_widget(bind_widget_dict,widget,attr_name)


        for attr_name, attr_desc in attrs.items():
        # TODO: отработать вариант, когда несколько полей привязаны к одному виджету (таблица),
        # TODO: сохранить порядок таких полей
            if widgets := (
                set(bind_widgets_kw.get(attr_name, set()))
                | set(bind_widget_dict.get(attr_name, set()))
                | set(t.cast(QWidget, getattr(parent_widget, attr_name, set())))
            ):
                if prev := self.attrs.get(attr_name, None):
                    prev = SetWidget(prev.widgets | widgets, prev.attr_desc)
                else:
                    prev = SetWidget(widgets, attr_desc)

                self.attrs[attr_name] = prev

        super().__init__(len(objects), len(self.attrs), parent_widget)

        self.mapper = QDataWidgetMapper()
        self.mapper.setModel(self)
        index = itertools.count(1)
        for _, (widgets, attr_desc) in self.attrs.items():
            for widget in widgets:
                self.mapper.addMapping(widget, next(index))
        self.mapper.toFirst()

    def get_common_attrs(self):
        ...

    def data(self, index: QModelIndex, role: Qt.ItemDataRole) -> t.Any:
        file = self.items[index.row()]
        field = self.attrs_name[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            value = getattr(file, field)
            return value
        elif role == Qt.ItemDataRole.EditRole:
            if field == "content":
                value = file.get_content()
            else:
                value = getattr(file, field)
            return value

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignCenter
        elif role == Qt.ItemDataRole.DecorationRole:
            return QColor(Qt.GlobalColor.green if not file.readonly else Qt.GlobalColor.red)
        elif role == Qt.ItemDataRole.ForegroundRole:
            # return QColor(Qt.GlobalColor.green if not file.readonly else Qt.GlobalColor.red)
            return QColor(
                Qt.GlobalColor.darkGreen if not file.readonly else Qt.GlobalColor.darkRed
            )


class DesktopFilesModel(QStandardItemModel):
    def __init__(self, files: list[DesktopFile], parent: t.Optional[QObject] = None) -> None:
        self.files = files
        self.fields = list(DesktopFile.__annotations__.keys())
        self.fields.append("content")
        super().__init__(len(self.files), len(self.fields), parent)

    def bindWidget(self, widget: QWidget, ext_fields: list[QWidget]):
        self.mapper = QDataWidgetMapper()
        # self.children()
        self.mapper.setModel(self)
        # self.mapper.currentIndexChanged.connect(self.mapper_index_changed)
        for field_name in self.fields:
            if not (attr := getattr(widget, field_name, None)):
                continue
            self.mapper.addMapping(attr, self.fields.index(field_name) + 1)
        for field in ext_fields:
            self.mapper.addMapping(field, len(self.fields))
        self.mapper.toFirst()

    def data(self, index: QModelIndex, role: Qt.ItemDataRole) -> t.Any:
        file = self.files[index.row()]
        field = self.fields[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            value = getattr(file, field)
            return value
        elif role == Qt.ItemDataRole.EditRole:
            if field == "content":
                value = file.get_content()
            else:
                value = getattr(file, field)
            return value

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignCenter
        elif role == Qt.ItemDataRole.DecorationRole:
            return QColor(Qt.GlobalColor.green if not file.readonly else Qt.GlobalColor.red)
        elif role == Qt.ItemDataRole.ForegroundRole:
            # return QColor(Qt.GlobalColor.green if not file.readonly else Qt.GlobalColor.red)
            return QColor(
                Qt.GlobalColor.darkGreen if not file.readonly else Qt.GlobalColor.darkRed
            )

    # def setData(self, index, value, role) -> bool:
    #     # if role == Qt.ItemDataRole.DisplayRole:
    #     setattr(self.files[index.row()], self.fields[index.column()], value)

    # def rowCount(self, parent: QModelIndex) -> int:
    #     return len(self.files)

    # def columnCount(self, parent) -> int:
    #     return len(self.fields)


class MainWindow(QMainWindow):
    current_file: DesktopFile

    filesList: QListWidget

    files: QListView
    current_file_change = Signal(DesktopFile)

    def __init__(self, parent: t.Optional[QWidget] = None, *args) -> None:
        super().__init__(parent, *args)

        ui_filename = "tun1c_3.ui"
        ui_file = QFile(ui_filename)
        if not ui_file.open(QIODevice.OpenModeFlag.ReadOnly):
            print(f"Cannot open {ui_filename}:{ui_file.errorString()}")
            self.exit(-1)
        loadUi(ui_file, self)
        ui_file.close()

        self.fields = list(DesktopFile.__annotations__.keys())

        self.models = DesktopFilesModel(find_desktop_files())

        self.files.setModel(self.models)
        # self.files.setModelColumn(0)

        self.mapper = QDataWidgetMapper()
        # self.children()
        self.mapper.setModel(self.models)
        # self.mapper.currentIndexChanged.connect(self.mapper_index_changed)
        for field_name in self.fields:
            if not (attr := getattr(self, field_name, None)):
                continue
            self.mapper.addMapping(attr, self.fields.index(field_name) + 1)
        self.mapper.addMapping(self.content, len(self.fields))
        self.mapper.toFirst()
        # self.mapper.setCurrentModelIndex()
        # self.files.activated.connect(self.mapper.setCurrentModelIndex)
        # self.files.activated.connect(self.on_model_selectrow)

        files_sel_model = self.files.selectionModel()
        files_sel_model.currentRowChanged.connect(self.mapper.setCurrentModelIndex)
        # self.files.selectionModel().currentRowChanged.connect(self.on_model_selectrow)
        # files_sel_model.setCurrentIndex(files_sel_model.selectedRows(0)[0],QtCore.QItemSelectionModel.SelectionFlag.SelectCurrent)
        # files_sel_model.

        #         #tuple(self.files.keys()).index(self.current_file.filename)
        #     #)

    # def on_model_selectrow(self, index: QModelIndex):
    #     #print(index)
    #     self.mapper.setCurrentModelIndex(index)

    # def mapper_index_changed(self, index:int):
    #    print(index)

    # def on_files_current_row_change(self):
    #    self.current_file_change.emit(self.current_file)

    def on_create_new_file_btn_clicked(self):
        ...

    def on_files_list_currentRowChanged(self, row: int):
        ...

    def on_execute_btn_clicked(self):
        ...

    def on_exit_btn_clicked(self):
        ...

    def on_set_scale_stateChanged(self, state: int):
        ...

    def on_scale_valueChanged(self, value: int):
        ...

    def on_set_dpi_scale_stateChanged(self, state: int):
        ...

    def on_dpi_scale_valueChanged(self, value: int):
        ...

    def on_name_ru_textChanged(self, value: str):
        pass

    def on_content_editor__textChanged(self):
        pass


class Application(QApplication):
    ui: QtWidgets.QMainWindow
    current_file: DesktopFile

    message = Signal()

    def __init__(self, *args):

        super().__init__([])

        mw = MainWindow()

        ui_filename = "tun1c_2.ui"
        ui_file = QFile(ui_filename)
        if not ui_file.open(QIODevice.ReadOnly):
            print(f"Cannot open {ui_filename}:{ui_file.errorString()}")
            self.exit(-1)
        loader = QUiLoader()
        self.ui = loader.load(ui_file)
        ui_file.close()

        self.ui.set_scale.toggled.connect(self._on_change_value)
        self.ui.set_dpi_scale.toggled.connect(self._on_change_value)
        self.ui.scale.valueChanged.connect(self._on_change_value)
        self.ui.dpi_scale.valueChanged.connect(self._on_change_value)

        self.ui.name_en.textEdited.connect(self._on_change_value)
        self.ui.name_en.textEdited.connect(self._on_change_value)
        self.ui.command.textEdited.connect(self._on_change_value)

        self.ui.contentTextEdit.textChanged.connect(self._on_change_value)

        self.ui.preload_libstdc.toggled.connect(self._on_change_value)

        self.ui.execute_button.pressed.connect(self._on_execute_button)
        self.ui.files.currentRowChanged.connect(self._on_file_select)
        # self.ui.openfile_button.pressed.connect(self._on_openfile)

        self.ui.createNewBox.setVisible(False)
        self.ui.createNewButton.pressed.connect(self._on_create_new_button)
        self.ui.removeButton.pressed.connect(self._on_remove_file_button)
        # self.ui.createNewButton.pressed.connect(self._on_create_new_button)

        files = find_desktop_files()
        self.files = {file.filename: file for file in files}
        # for file in files:

        # with QDeepSignalBlocker(self.ui.files):
        u_files: QListWidget = self.ui.files
        for path, file in self.files.items():
            item = QListWidgetItem(path)
            if file.readonly:
                item.setBackground(QColor(Qt.GlobalColor.lightGray))
            u_files.addItem(item)
            # self.ui.files.addItems(self.files.keys())

        self._setFile(files[0] if len(files) > 0 else None)

    _ui_mode: t.Literal["normal", "new_file"] = "normal"

    def _on_create_new_button(self):
        self._ui_mode = "new_file"
        self.ui.createNewButton.pressed.connect(self._on_save_newfile_button)
        self.ui.removeButton.pressed.connect(self._on_cancel_newfile_button)
        self._update_ui()

    def _on_cancel_newfile_button(self):
        self._ui_mode = "normal"
        self.ui.createNewButton.pressed.connect(self._on_create_new_button)
        self.ui.removeButton.pressed.connect(self._on_remove_file_button)
        self._update_ui()

    def _on_save_newfile_button(self):
        filename = Path(
            str(Path.home().resolve())
            + "/.local/share/applications/1cestart"
            + self.ui.new_filename.text().strip()
            + ".desktop"
        )
        if filename.exists():
            # QMessageBox("Файл с таким именем уже существует").show()
            # QErrorMessage(self.ui.new_filename).showMessage("Файл с таким именем уже существует")
            QErrorMessage().showMessage("Файл с таким именем уже существует")

            return
        shutil.copyfile(self.current_file.filename, str(filename))
        file = DesktopFile(str(filename))
        self.files.update({file.filename: file})
        self.ui.files.addItem(QListWidgetItem(file.filename))
        self.current_file = file

        self._ui_mode = "normal"
        self.ui.createNewButton.pressed.connect(self._on_create_new_button)
        self.ui.removeButton.pressed.connect(self._on_remove_file_button)
        self._update_ui()

    def _on_remove_file_button(self):
        if self.current_file and not self.current_file.readonly:
            os.remove(self.current_file.filename)
            self.files.pop(self.current_file.filename)
            u_files: QListWidget = self.ui.files
            if item_files := u_files.findItems(
                self.current_file.filename, Qt.MatchFlag.MatchFixedString
            ):
                u_files.removeItemWidget(item_files[0])
            self.current_file = next(iter(self.files.values())) if len(self.files) else None
            self._update_ui()

    def _setFile(self, file: DesktopFile):
        self.current_file = file
        self._update_ui()

    def exec(self) -> int:
        self.ui.show()
        return super().exec()

    def _on_change_value(self, *_):
        self._load_data_from_ui()
        self._update_ui()

    def _on_openfile(self, *_):
        subprocess.call(("xdg-open", self.current_file.filename))

    def _on_execute_button(self, *_):
        with subprocess.Popen(self.current_file.exec_command, env=os.environ, shell=True) as _:
            # _.wait()
            ...

    # def _disable_widget(widget: QWidget):
    #     def disable():
    #         if "setReadOnly" in dir(widget):
    #             widget

    def _update_ui(self):
        def setEnabled(widget: QtWidgets, state: bool):
            # enabled = widget.isEnabled()
            widget.setEnabled(not state)
            widget.setEnabled(state)

        def setReadOnly(widget: QtWidgets, state: bool):
            # enabled = widget.isEnabled()
            widget.setReadOnly(not state)
            widget.setReadOnly(state)

        with QDeepSignalBlocker(self.ui):
            if self._ui_mode == "normal":
                self.ui.createNewBox.setVisible(False)
                self.ui.execute_button.setVisible(True)
                # self.ui.createNewBox.setVisible(False)
                self.ui.createNewButton.setText("Создать по образцу текущего")

                self.ui.updateButton.setVisible(True)
                self.ui.removeButton.setText("Удалить")
                self.ui.removeButton.setEnabled(
                    self.current_file and not self.current_file.readonly
                )
                self.ui.files.setEnabled(True)

                if self.current_file:
                    self.ui.files.setCurrentRow(
                        tuple(self.files.keys()).index(self.current_file.filename)
                    )
                    if (
                        text := self.current_file.get_content()
                    ) != self.ui.contentTextEdit.toPlainText():
                        cursor = self.ui.contentTextEdit.textCursor()
                        pos = cursor.position()
                        self.ui.contentTextEdit.setPlainText(text)
                        setEnabled(self.ui.contentTextEdit, True)
                        cursor.setPosition(pos)
                        self.ui.contentTextEdit.setTextCursor(cursor)

                    self.ui.set_scale.setChecked(self.current_file.set_scale)
                    self.ui.scale.setValue(self.current_file.scale)
                    self.ui.scale.setEnabled(self.current_file.set_scale)
                    self.ui.set_dpi_scale.setChecked(self.current_file.set_dpi_scale)
                    self.ui.dpi_scale.setValue(self.current_file.dpi_scale)
                    self.ui.dpi_scale.setEnabled(self.current_file.set_dpi_scale)
                    self.ui.preload_libstdc.setChecked(self.current_file.preload_libstdc)
                    self.ui.name_ru.setText(self.current_file.name_ru)
                    self.ui.name_en.setText(self.current_file.name_en)
                    self.ui.command.setText(self.current_file.command)
                    # self.ui.all_valuesFrame.setEnabled(True)
                    # self.ui.all_valuesFrame.setEnabled(self.current_file.readonly)
                    setEnabled(self.ui.all_valuesFrame, not self.current_file.readonly)
                    setEnabled(self.ui.contentTextEdit, True)
                    setReadOnly(self.ui.contentTextEdit, self.current_file.readonly)
                else:
                    self.ui.files.setCurrentRow(-1)
                    self.ui.all_valuesFrame.setEnabled(False)
            else:
                self.ui.createNewBox.setVisible(True)
                self.ui.createNewButton.setText("Сохранить новый файл")
                self.ui.execute_button.setVisible(False)
                self.ui.updateButton.setVisible(False)
                self.ui.removeButton.setText("Отменить")
                self.ui.files.setEnabled(False)
                self.ui.all_valuesFrame.setEnabled(False)
                self.ui.pathLabel.setText(
                    str(Path.home().absolute()) + "/.local/share/applications/1cestart"
                )

    def _load_data_from_ui(self, *_):
        self.current_file.set_scale = self.ui.set_scale.isChecked()
        self.current_file.scale = int(
            round(self.ui.scale.value() if self.current_file.set_scale else 1, 0)
        )

        self.current_file.set_dpi_scale = self.ui.set_dpi_scale.isChecked()
        self.current_file.dpi_scale = round(
            self.ui.dpi_scale.value() if self.current_file.set_dpi_scale else 1.0, 1
        )

        self.current_file.preload_libstdc = self.ui.preload_libstdc.isChecked()
        self.current_file.name_ru = self.ui.name_ru.text()
        self.current_file.command = self.ui.command.text()

        self.current_file.set_content(self.ui.contentTextEdit.toPlainText())

    def _on_file_select(self, *_):
        self._setFile(self.files[self.ui.files.currentItem().text()])

    def _on_exit_button(self):
        self.exit()


if __name__ == "__main__":

    # QtWidgets.QApplication.setAttribute(
    #     QtCore.Qt.AA_EnableHighDpiScaling, True
    # )  # enable highdpi scaling
    # QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)  # use highdpi icons

    # QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_ShareOpenGLContexts)
    # app = Application()
    app = QApplication([])
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
