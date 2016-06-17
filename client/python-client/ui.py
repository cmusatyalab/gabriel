#!/usr/bin/env python

from PyQt4 import QtGui, QtCore
from PyQt4.QtCore import QThread, SIGNAL, pyqtSignal
from PyQt4.QtGui import QPixmap, QImage, QMessageBox, QVBoxLayout
import threading
import sys  # We need sys so that we can pass argv to QApplication
import design  # This file holds our MainWindow and all design related things
import os  # For listing directory methods
import client
import numpy as np
import re
import pdb

class UI(QtGui.QMainWindow, design.Ui_MainWindow):
    def __init__(self):
        super(self.__class__, self).__init__()
        self.setupUi(self)  # This is defined in design.py file automatically

    def set_image(self, frame):
        img = QImage(frame, frame.shape[1], frame.shape[0], QtGui.QImage.Format_RGB888)
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
        client.alive=False
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
