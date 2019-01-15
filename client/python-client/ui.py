#!/usr/bin/env python
from __future__ import absolute_import, division, print_function

import os  # For listing directory methods
import pdb
import re
import sys  # We need sys so that we can pass argv to QApplication
import threading

import numpy as np
from PyQt4 import QtCore, QtGui
from PyQt4.QtCore import SIGNAL, QThread, pyqtSignal
from PyQt4.QtGui import QImage, QMessageBox, QPixmap, QVBoxLayout

import client
import design  # This file holds our MainWindow and all design related things


class UI(QtGui.QMainWindow, design.Ui_MainWindow):
    def __init__(self):
        super(self.__class__, self).__init__()
        self.setupUi(self)  # This is defined in design.py file automatically

    def set_image(self, frame):
        img = QImage(
            frame, frame.shape[1], frame.shape[0], QtGui.QImage.Format_RGB888)
        pix = QPixmap.fromImage(img)
        self.label_image.setPixmap(pix)


class ClientThread(QThread):
    sig_frame_available = pyqtSignal(object)

    def __init__(self):
        super(self.__class__, self).__init__()
        self._stop = threading.Event()

    def run(self):
        client.run(self.sig_frame_available)

    def stop(self):
        client.alive = False
        self._stop.set()


def main():
    app = QtGui.QApplication(sys.argv)
    ui = UI()
    ui.show()
    clientThread = ClientThread()
    clientThread.sig_frame_available.connect(ui.set_image)
    clientThread.finished.connect(app.exit)
    clientThread.start()

    sys.exit(app.exec_())  # and execute the app


if __name__ == '__main__':  # if we're running file directly and not importing it
    main()
