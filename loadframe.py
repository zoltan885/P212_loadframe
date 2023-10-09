#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Oct  9 20:42:04 2023

@author: hegedues
"""

import sys
import os
import time
import logging
logging.getLogger().setLevel(logging.DEBUG)

import glob
from watchdog.observers import Observer
from watchdog.events import LoggingEventHandler
from watchdog.events import FileSystemEventHandler
import watchdog.events
import watchdog.observers
import pyFAI, fabio
from pyFAI import AzimuthalIntegrator
import multiprocessing
from multiprocessing import Event, Process, Queue
import queue


TANGO = False
try:
    import PyTango as PT
    import HasyUtils as HU
    TANGO = True
except ImportError as e:
    logging.error(f"{e}")


from PyQt5 import QtWidgets, uic, QtGui
from PyQt5.QtCore import QRunnable, Qt, QThreadPool, pyqtSignal, QThread, QObject, pyqtSlot
from PyQt5.QtWidgets import (
    QMainWindow,
    QLabel,
    QGridLayout,
    QWidget,
    QPushButton,
    QProgressBar,
    QFileDialog,
    )

TEST = True


def _getMovableSpockNames():
    '''
       gets the stepping_motor devices from the online.xml together with the host name
    '''
    if not TANGO:
        lst = ['a', 'b']
        return lst
    names=dict()
    try:
        for rec in HU.getOnlineXML():
            # the phy_motion is type_tango
            if rec['type'].lower() == 'stepping_motor' or rec['type'].lower() == 'type_tango':
                host = rec['hostname'].lower()
                device = rec['device'].lower()
                names[rec['name'].lower()] = host + '/' + device
                #names[rec['name'].lower()] = rec['device'].lower()
    except:
        pass
    return names


class _voltageDevices():
    def __init__(self):
        self.dev = []
        self.dev.append(('hasep21eh3:10000/p21/keithley2602/eh3.1', 'MeasureVoltage'))
        self.dev.append(('hasep21eh3:10000/p21/keithley2602/eh3.2', 'MeasureVoltage'))
    def devices(self):
        return self.dev
    def printed(self):
        return [str(a)+'/'+str(b) for a,b in self.dev]


class loadcell():
    def __init__(self, typ, tangoAttr, positiveDirection='Tension'):
        self.updateType(typ)
        self.cell1mul = -193.913
        self.cell5mul = -1017.78
        self.cell1zeroVoltage = 6.96
        self.cell5zeroVoltage = 5.99
        self.voltage = tangoAttr
        self.positiveDirection = positiveDirection

    def updateType(self, typ):
        assert typ in ['1 kN', '5 kN'], 'No such loadcell'
        self.type = typ

    def updateDirection(self, direction):
        assert direction in ['Tension', 'Compression'], 'Wrong tension-compression direction'
        self.positiveDirection = direction
        if self.type == '1 kN':
            if self.positiveDirection=='Tension':
                self.cell1mul = -193.913
            elif self.positiveDirection=='Compression':
                self.cell1mul = 193.913
        elif self.type == '5 kN':
            if self.positiveDirection=='Tension':
                self.cell5mul = -1017.78
            elif self.positiveDirection=='Compression':
                self.cell5mul = 1017.78

    def getEq(self):
        if self.type == '1 kN':
            return 'F[N] = %.1f * (U[V]-%.3f)' % (self.cell1mul, self.cell1zeroVoltage)
        elif self.type == '5 kN':
            return 'F[N] = %.1f * (U[V]-%.3f)' % (self.cell5mul, self.cell5zeroVoltage)

    def calibrate(self):
        if TEST:
            val = 16.8
        else:
            val = self.voltage.read().value
        if self.type == '1 kN':
            self.cell1zeroVoltage = val
        elif self.type == '5 kN':
            self.cell5zeroVoltage = val

    @property
    def force(self):
        if self.type == '1 kN':
            return (self.voltage.read().value-self.cell1zeroVoltage) * self.cell1mul
        elif self.type == '5 kN':
            return (self.voltage.read().value-self.cell5zeroVoltage) * self.cell5mul





class MainWidget(QtWidgets.QWidget):
    def __init__(self, *args, **kwargs):
        super(MainWidget, self).__init__(*args, **kwargs)
        uic.loadUi('loadframe.ui', self)

        self.crossheadMotorDev = None
        self.loadcellVoltageDev = None

        # add voltage devices:
        self.comboBox_loadcellVoltage.addItems(_voltageDevices().printed())
        # add movable devices:
        self.comboBox_crossheadMotor.addItems(_getMovableSpockNames())

        # define loadCell
        self.loadCell = loadcell(self.comboBox_loadcell.currentText(), 'attr')
        self.label_conversionEq.setText(self.loadCell.getEq())

        self.pushButton_connectToDevices.clicked.connect(self._connectToDevices)
        self.comboBox_loadcell.currentIndexChanged.connect(self.updateConversionEq)

        self.pushButton_zeroVoltageCalibration.clicked.connect(self.calibrateZeroVoltage)

        self.comboBox_positiveDirection.currentIndexChanged.connect(self.updateTensionCompression)


    def _connectToDevices(self):
        cMD = self.comboBox_crossheadMotor.currentText()
        lVD = self.comboBox_loadcellVoltage.currentText()
        logging.info(f"cMD: {cMD}")
        logging.info(f"lVD: {lVD}")
        if not TANGO:
            self.label_connectionStatus.setText('TEST CONNECTION ESTABLISHED')
            self.pushButton_zeroVoltageCalibration.setEnabled(True)
            return
        try:
            self.crossheadMotorDev = PT.DeviceProxy(cMD)
        except:
            logging.error(f"Could not instantiate device {cMD}")
        try:
            self.loadcellVoltageDev = PT.DeviceProxy(lVD)
        except:
            logging.error(f"Could not instantiate device {lVD}")
        if self.crossheadMotorDev is not None and self.loadcellVoltageDev is not None:
            self.label_connectionStatus.setText('CONNECTION ESTABLISHED')
        self.pushButton_zeroVoltageCalibration.setEnabled(True)

    def updateConversionEq(self):
        self.loadCell.updateType(self.comboBox_loadcell.currentText())
        self.label_conversionEq.setText(self.loadCell.getEq())

    def calibrateZeroVoltage(self):
        self.loadCell.calibrate()
        self.updateConversionEq()

    def updateTensionCompression(self):
        self.loadCell.updateDirection(self.comboBox_positiveDirection.currentText())
        self.updateConversionEq()








def mainGUI():
    app = QtWidgets.QApplication(sys.argv)
    main = MainWidget()
    main.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    mainGUI()
