#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Oct  9 20:42:04 2023

@author: hegedues
"""

import sys
import os
import time
import datetime
import logging
logFormatter = logging.Formatter("%(asctime)-25.25s %(threadName)-12.12s %(name)-25.24s %(levelname)-10.10s %(message)s")
rootLogger = logging.getLogger()
rootLogger.setLevel(logging.DEBUG)
#logging.getLogger().setLevel(logging.DEBUG)
fileHandler = logging.FileHandler(os.path.join(os.getcwd(), 'log.log'))
fileHandler.setFormatter(logFormatter)
rootLogger.addHandler(fileHandler)

consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(logFormatter)
rootLogger.addHandler(consoleHandler)


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
from threading import Thread
import queue



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
TANGO = False
try:
    import PyTango as PT
    import HasyUtils as HU
    TANGO = True
except ImportError as e:
    logging.error(f"{e}")


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


class Loadcell():
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


class Sample():
    def __init__(self):
        self._thickness = None
        self._width = None
        self._gaugeLength = None
        self.name = None
        self.description = None

    def setGeometry(self, thickness, width, gaugeLength):
        # this intentionally uses the setters so that any logic only needs to be coded there
        self.thickness = thickness
        self.width = width
        self.gaugeLength = gaugeLength

    def report(self):
        logging.info(f"Sample name: {self.name}")
        logging.info(f"Description: {self.description}")
        logging.info(f"Sample geometry: width {self.width:.2f} thickness {self.thickness:.2f} gaugeLength {self.gaugeLength:.2f}")

    @property
    def thickness(self):
        return self._thickness

    @thickness.setter
    def thickness(self, value):
        assert value >= 0.01, 'Cannot set smaller than 0.01 mm thickness'
        self._thickness = value

    @property
    def width(self):
        return self._width

    @width.setter
    def width(self, value):
        self._width = value

    @property
    def gaugeLength(self):
        return self._gaugeLength

    @gaugeLength.setter
    def gaugeLength(self, value):
        assert value >= 3, 'Cannot set smaller than 3 mm gauge length'
        assert value <= 20, 'Cannot set larger than 20 mm gauge length'
        self._gaugeLength = value

    @property
    def crossection(self):
        return self.width * self.thickness

    def stress(self, forceN):
        '''
        Parameters
        ----------
        forceN : Float
            Current force on the sample in N

        Returns
        -------
        Float
            Current stress in MPa

        '''
        return 1e6*forceN/self.crossection

    def strain(self, displacementMM):
        '''
        Parameters
        ----------
        displacementMM : Float
            Displacement in mm

        Returns
        -------
        Float
            strain, no unit

        '''
        return displacementMM/self.gaugeLength





class DataLogger():
    def __init__(self, timeformat=None):
        self._logfile = None
        self._loggrace = 0.5
        self._attrs = {}
        self._timeformat = 'unix'
        if timeformat is not None:
            self.timeformat = timeformat # this should already use the setter and check for possible options
        self.startEv = Event()
        self.stopEv = Event()
        self.loggerThread = Thread(target=self.loggerThread, args=(),
                                   kwargs={'startEv': self.startEv,
                                           'stopEv':self.stopEv})
        self.loggerThread.start()

    @property
    def logfile(self):
        return self._logfile

    @logfile.setter
    def logfile(self, value):
        assert os.path.isabs(value), 'Absoulte path required'
        if os.path.exists(value):
            logging.warning('Logfile already existed. Appending.')
        self._logfile = value

    @property
    def timeformat(self):
        return self._timeformat

    @timeformat.setter
    def timeformat(self, value):
        assert value in ['unix', 'iso'], 'Timeformat can only be unix or iso'
        self._timeformat = value


    def addLogAttr(self, name, attr):
        self._attrs[name] = attr

    def loggerThread(self, startEv=None, stopEv=None):
        logging.warning('Waiting for start signal')
        startEv.wait()
        while not stopEv.is_set():
            with open(self.logfile, 'a') as log:
                if self.timeformat == 'unix':
                    log.write(f"time.time() ")
                elif self.timeformat == 'iso':
                    log.write(f"{datetime.datetime.now().isoformat()} ")
                for k,v in self._attrs.items():
                    log.write(f"{v} ")
                log.write("\n")
            #logging.warning('Logged a line')
            time.sleep(self._loggrace)










class MainWidget(QtWidgets.QWidget):
    def __init__(self, *args, **kwargs):
        super(MainWidget, self).__init__(*args, **kwargs)
        uic.loadUi('loadframe.ui', self)

        #
        # Configuration
        #
        self.crossheadMotorDev = None
        self.loadcellVoltageDev = None
        # add voltage devices:
        self.comboBox_loadcellVoltage.addItems(_voltageDevices().printed())
        # add movable devices:
        self.comboBox_crossheadMotor.addItems(_getMovableSpockNames())
        # define loadCell
        self.loadCell = Loadcell(self.comboBox_loadcell.currentText(), 'attr')
        self.label_conversionEq.setText(self.loadCell.getEq())
        # signals
        self.pushButton_connectToDevices.clicked.connect(self._connectToDevices)
        self.comboBox_loadcell.currentIndexChanged.connect(self.updateConversionEq)
        self.pushButton_zeroVoltageCalibration.clicked.connect(self.calibrateZeroVoltage)
        self.comboBox_positiveDirection.currentIndexChanged.connect(self.updateTensionCompression)

        #
        # Sample
        #
        self.sample = Sample()
        self.pushButton_updateSample.clicked.connect(self.updateSample)

        self.lineEdit_logfile.setText(os.path.join(os.getcwd(), 'log.log'))



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

    def updateSample(self):
        th = self.doubleSpinBox_thickness.value()
        w = self.doubleSpinBox_width.value()
        gl = self.doubleSpinBox_gaugeLength.value()
        self.sample.setGeometry(th, w, gl)
        self.sample.name = self.lineEdit_sampleName.text()
        self.sample.description = self.lineEdit_sampleDescription.text()
        self.label_thicknessCurrent.setText(f"current value: {self.sample.thickness:.2f} mm")
        self.label_widthCurrent.setText(f"current value: {self.sample.width:.2f} mm")
        self.label_gaugeCurrent.setText(f"current value: {self.sample.gaugeLength:.2f} mm")
        self.sample.report()













def mainGUI():
    app = QtWidgets.QApplication(sys.argv)
    main = MainWidget()
    main.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    mainGUI()
