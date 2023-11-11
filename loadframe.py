#!/home/hegedues/miniforge3/envs/pyfai/bin/python
# -*- coding: utf-8 -*-
"""
Created on Mon Oct  9 20:42:04 2023

@author: hegedues
"""

import sys
import os
import psutil
import time
import datetime
import logging
logFormatter = logging.Formatter("%(asctime)-25.25s %(threadName)-12.12s %(name)-25.24s %(levelname)-10.10s %(message)s")
rootLogger = logging.getLogger()
rootLogger.setLevel(logging.INFO)
#logging.getLogger().setLevel(logging.DEBUG)
fileHandler = logging.FileHandler(os.path.join(os.getcwd(), 'log.log'))
fileHandler.setFormatter(logFormatter)
rootLogger.addHandler(fileHandler)

consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(logFormatter)
rootLogger.addHandler(consoleHandler)


import glob
# from watchdog.observers import Observer
# from watchdog.events import LoggingEventHandler
# from watchdog.events import FileSystemEventHandler
# import watchdog.events
# import watchdog.observers
# import pyFAI, fabio
# from pyFAI import AzimuthalIntegrator
import multiprocessing
from multiprocessing import Event, Process, Queue
from threading import Thread
import queue



from PyQt5 import QtWidgets, uic, QtGui
from PyQt5.QtCore import QRunnable, Qt, QThreadPool, pyqtSignal, QThread, QObject, pyqtSlot, QTimer
from PyQt5.QtWidgets import (
    QMainWindow,
    QLabel,
    QGridLayout,
    QWidget,
    QPushButton,
    QProgressBar,
    QFileDialog,
    )


import subprocess, shlex
TEST = True
if TEST:
    logging.info('Starting test Tango server')
    #testTangoServer = subprocess.Popen(shlex.split('./testserver1.py test1 -dlist p21/keithley2602b/eh3.01,p21/motor/eh3_u4.15 -nodb -host hasmhegedues -port 10000'))
    testTangoServer = subprocess.Popen(['bash','startServer.sh'])
    logging.info(f'Test Tango server started. PID: {testTangoServer.pid}')
    testServerObserver = subprocess.Popen(['xterm', './testserverWatch.py'])
    logging.info(f'Test Tango server observer started. PID: {testServerObserver.pid}')

    # if not stopped properly it reserves the port
    # sudo lsof -i :10000

TANGO = False
HUImported = False
try:
    import PyTango as PT
    TANGO = True
except ImportError as e:
    logging.warning(f"{e}")

try:
    import HasyUtils as HU
    HUImported = True
except ImportError as e:
    logging.warning(f"{e}")


def _getMovableSpockNames():
    '''
       gets the stepping_motor devices from the online.xml together with the host name
    '''
    if not HUImported and not TEST:
        dct = {'crosshead': 'hasep21eh3:10000/p21/motor/eh3_u4.15', 'dummy': 'hasep21eh3:10000/p21/motor/eh3_u4.99'}
        return dct
    if not HUImported and TEST:
        dct = {'crosshead': 'tango://hasmhegedues:10000/p21/motor/eh3_u4.15#dbase=no'}
        return dct
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

_TangoStateColors = {'ON': '#42f545',
                     'OFF': '#f4f7f2',
                     'MOVING': '#427ef5',
                     'STANDBY': '#f5f253',
                     'FAULT': '#cc2b2b',
                     'INIT': '#daa06d',
                     'ALARM': '#eb962f',
                     'DISABLE': '#f037fa',
                     'UNKNOWN': '#808080'}


_voltageAttrList = ['hasep21eh3:10000/p21/keithley2602b/eh3.01/MeasVoltage',
                    'hasep21eh3:10000/p21/keithley2602b/eh3.02/MeasVoltage',
    ]

if TEST:
    _voltageAttrList = ['tango://hasmhegedues:10000/p21/keithley2602b/eh3.01/voltage#dbase=no',
                        ]

# TODO polling should be done from here and not from the logger
class Loadcell():
    def __init__(self, typ='1 kN', attr=None, positiveDirection='Tension'):
        self.cell1mul = -193.913
        self.cell5mul = -1017.78
        self.cell1zeroVoltage = 6.96
        self.cell5zeroVoltage = 5.99
        self._currentZeroVoltage = None
        self.positiveDirection = positiveDirection
        if typ is not None:
            self.updateType(typ)
        if attr is not None:
            self.updateVoltageAttr(attr)


    def updateVoltageAttr(self, attr):
        if TEST:
            self.attrProxy = PT.AttributeProxy('tango://hasmhegedues:10000/p21/keithley2602b/eh3.01/voltage#dbase=no')
        try:
            self.attrProxy = PT.AttributeProxy(attr)
        except Exception as e:
            raise e

    @property
    def voltage(self):
        return self.attrProxy.read().value

    def updateType(self, typ):
        assert typ in ['1 kN', '5 kN'], 'No such loadcell'
        self.type = typ
        if typ == '1 kN':
            self._currentZeroVoltage = self.cell1zeroVoltage
        elif typ == '5 kN':
            self._currentZeroVoltage = self.cell5zeroVoltage

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
            if self.cell1zeroVoltage < 0:
                return 'F[N] = %.1f * (U[V]+%.3f)' % (self.cell1mul, -1*self.cell1zeroVoltage)
            else:
                return 'F[N] = %.1f * (U[V]-%.3f)' % (self.cell1mul, self.cell1zeroVoltage)
        elif self.type == '5 kN':
            if self.cell5zeroVoltage < 0:
                return 'F[N] = %.1f * (U[V]+%.3f)' % (self.cell5mul, -1*self.cell5zeroVoltage)
            return 'F[N] = %.1f * (U[V]-%.3f)' % (self.cell5mul, self.cell5zeroVoltage)

    def calibrate(self, val):
        if self.type == '1 kN':
            self.cell1zeroVoltage = val
        elif self.type == '5 kN':
            self.cell5zeroVoltage = val
        self._currentZeroVoltage = val

    @property
    def zeroVoltage(self):
        return self._currentZeroVoltage

    # @property
    # def force(self):
    #     if self.type == '1 kN':
    #         return (self.voltage.read().value-self.cell1zeroVoltage) * self.cell1mul
    #     elif self.type == '5 kN':
    #         return (self.voltage.read().value-self.cell5zeroVoltage) * self.cell5mul

    def force2(self, voltage):
        if self.type == '1 kN':
            return (voltage-self.cell1zeroVoltage) * self.cell1mul
        elif self.type == '5 kN':
            return (voltage-self.cell5zeroVoltage) * self.cell5mul






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
        return forceN/self.crossection

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


# TODO polling should be done from here and not from the logger
class Crosshead():
    '''
    dev is a the name of the device to create the device proxy
    '''
    def __init__(self, devname=None):
        self._device = None
        self._speed = None
        self._position = None
        if devname is not None:
            self._dev = devname

    @property
    def device(self):
        return self._device

    @device.setter
    def device(self, value):
        try:
            self._device = PT.DeviceProxy(value)
            logging.info('Crosshead device proxy created')
        except Exception as e:
            logging.error(e)
            raise e

    @property
    def position(self):
        return self.device.position

    @property
    def speed(self):
        speed = self.device.slewRate / self.device.conversion
        self._speed = speed
        return speed

    @speed.setter
    def speed(self, value):
        assert (value >= 0.5 and value <= 1000), 'Speed has to be in the range [0.5, 1000]'
        self.device.slewRate = value * self.device.conversion
        self._speed = value

    @property
    def state(self):
        # for testing
        if hasattr(self._device, 'st'):
            #logging.info(f'Trying to return st: {self._device.st}')
            return self._device.st
        else:
            return self._device.state()
            #logging.info('Trying to return state')

    def calibrate(self):
        self.device.calibrate(0)

    def moveto(self, pos):
        if TEST:
            self.device.moveto(pos)
            return
        self.device.position = (pos)



    def jog(self):
        pass



class DataLogger():
    validTimeformats = ['unix', 'iso', 'both']
    def __init__(self, timeformat=None):
        self._logfile = None
        self._logGrace = 0.05
        self._writeGrace = 0.5
        self._attrs = {}
        self._classAttrs = {}
        self._lastValues = {}
        self._timeformat = 'both'
        if timeformat is not None:
            self.timeformat = timeformat # already uses the setter to check for possible options
        self.startEv = Event()
        self.stopEv = Event()
        self.loggerThread = Thread(target=self.loggerThread, args=(),
                                   kwargs={'startEv': self.startEv,
                                           'stopEv':self.stopEv})
        self.loggerThread.start()
        self.writerThread = Thread(target=self.writerThread, args=(),
                                   kwargs={'startEv': self.startEv,
                                           'stopEv':self.stopEv})
        self.writerThread.start()


    @property
    def logfile(self):
        return self._logfile

    @logfile.setter
    def logfile(self, value):
        assert os.path.isabs(value), 'Absoulte path required'
        if os.path.exists(value):
            logging.warning('Logfile already existed. Appending.')
        self._logfile = value
        logging.info(f'Data log set to {self.logfile}')

    @property
    def timeformat(self):
        return self._timeformat

    @timeformat.setter
    def timeformat(self, value):
        assert value in self.validTimeformats, 'Timeformat can only be unix or iso'
        self._timeformat = value


    def addLogAttr(self, name, attr):  # Tango attributes
        '''
        add attribute to logging
        name: the display name of the attribute
        attr: the full 'TangoPath' of the attribute
        '''
        try:
            a = PT.AttributeProxy(attr)
            self._attrs[name] = a
            self._lastValues[name] = None
            logging.info(f'Attribute {name} : {attr} added to logging')
        except:
            logging.error(f'Failed to add attribute {name} : {attr} logging')

    def addClassAttr(self, name, obj, attr):  # for devices having their own classes implemented
        '''
        add attribute of an object to logging
        name: the display name of the attribute
        attr: the full 'TangoPath' of the attribute
        '''
        self._classAttrs[name] = (obj, attr)
        self._lastValues[name] = None
        logging.info(f'class {obj} attribute {attr} added to logging')


    def addCalculated(self, name, query_value):
        pass


    def _writeHeader(self):
        with open(self.logfile, 'a') as log:
            log.write('#')
            if self.timeformat == 'unix':
                log.write("UnixTime ")
            elif self.timeformat == 'iso':
                log.write("ISOTime ")
            elif self.timeformat == 'both':
                log.write("ISOTime UnixTime ")
            for k in self._classAttrs.keys():
                log.write(f"{k} ")
            for k in self._attrs.keys():
                log.write(f"{k} ")
            log.write("\n")

    # TODO move the polling and last N values into the device classes.
    # The current solution slows down the logger thread

    def loggerThread(self, startEv=None, stopEv=None):
        logging.warning('DataLogger is waiting for start signal')
        startEv.wait()
        logging.info('DataLogger logging thread started')
        while not stopEv.is_set():
            for k,(obj, attr) in self._classAttrs.items():
                self._lastValues[k] = getattr(obj, attr)
            for k,v in self._attrs.items():
                self._lastValues[k] = v.read().value
            time.sleep(self._logGrace)
        logging.info('DataLogger logging thread stopped')


    def writerThread(self, startEv=None, stopEv=None):
        logging.warning('DataLogger writer thread is waiting for start signal')
        noLogAttrs = 0
        noClassLogAttrs = 0
        startEv.wait()  # how to stop it here?
        time.sleep(0.2) # grace period for loggerThread to fetch values
        logging.info('DataLogger writer thread started')
        while self.logfile is None:
            if stopEv.is_set():
                return
            time.sleep(0.1)
        logging.info(f'Logging to {self.logfile}')
        lastLogfile = self.logfile
        while not stopEv.is_set():
            if noLogAttrs != len(self._attrs.keys()) or noClassLogAttrs != len(self._classAttrs.keys()):  # update the attrs on the fly
                self._writeHeader()
                noLogAttrs = len(self._attrs.keys())
                noClassLogAttrs = len(self._classAttrs.keys())
            if lastLogfile != self.logfile:  # updata the logfile on the fly
                self._writeHeader()
                lastLogfile = self.logfile
            with open(self.logfile, 'a') as log:
                if self.timeformat == 'unix':
                    log.write(f"{time.time()} ")
                elif self.timeformat == 'iso':
                    log.write(f"{datetime.datetime.now().isoformat()} ")
                elif self.timeformat == 'both':
                    log.write(f"{datetime.datetime.now().isoformat()} {time.time()} ")
                for k,(obj, attr) in self._classAttrs.items():
                    log.write(f"{self._lastValues[k]} ")
                    #log.write(f"{getattr(obj, attr)} ")
                for k,v in self._attrs.items():
                    log.write(f"{self._lastValues[k]} ")
                log.write("\n")
            #logging.warning('Logged a line')
            time.sleep(self._writeGrace)
        logging.info('DataLogger writer thread stopped')






class DevicePoller(QObject):
    voltage = pyqtSignal(float)
    position = pyqtSignal(float)
    speed = pyqtSignal(float)
    fastPolling = 0.1
    slowPolling = 5

    def __init__(self, motDevProxy, loadCell):
        super(DevicePoller, self).__init__()
        self.motDevProxy = motDevProxy
        self.loadCell = loadCell

    def run(self):    # would be better to use QTimer
        logging.info('Device poller started')
        t0 = time.time()
        while True:
            self.voltage.emit(self.loadCell.voltage)
            logging.info(self.loadCell.voltage)
            self.position.emit(self.motDevProxy.position)
            if time.time()-t0 > self.slowPolling:
                sp = self.motDevProxy.slewRate / self.motDevProxy.conversion
                self.speed.emit(sp)
            time.sleep(self.fastPolling)




class MainWidget(QtWidgets.QWidget):
    def __init__(self, *args, **kwargs):
        super(MainWidget, self).__init__(*args, **kwargs)
        uic.loadUi('loadframe.ui', self)

        self.timerSlow=QTimer()
        self.timerSlow.start(1000)
        self.timerFast=QTimer()
        self.timerFast.start(100)

        # status label
        self.timerSlow.timeout.connect(self.updateRStatusLabel)
        self.timerFast.timeout.connect(self.updateLCDNums)
        self.timerFast.timeout.connect(self.updateDevStates)


        #
        # Configuration
        #
        self.crossheadMotorDev = None
        self.crosshead = Crosshead()
        # add voltage devices:
        self.comboBox_loadcellVoltage.addItems(_voltageAttrList)
        # add movable devices:
        self.comboBox_crossheadMotor.addItems([f'{k}->  {v}' for k,v in _getMovableSpockNames().items()])
        # define loadCell
        self.loadCell = Loadcell()
        self.label_conversionEq.setText(self.loadCell.getEq())
        # signals
        self.pushButton_connectToDevices.clicked.connect(self._connectToDevices)
        self.comboBox_loadcell.currentIndexChanged.connect(self.updateConversionEq)
        self.pushButton_zeroVoltageCalibration.clicked.connect(self.calibrateZeroVoltage)
        self.comboBox_positiveDirection.currentIndexChanged.connect(self.updateTensionCompressionSign)

        #
        # Sample
        #
        self.sample = Sample()
        self.pushButton_updateSample.clicked.connect(self.updateSample)

        self.lineEdit_logfile.setText(os.path.join(os.getcwd(), 'log.log'))
        self.lineEdit_dataLogfile.setText(os.path.join(os.getcwd(), 'data.log'))

        #
        # Data logger
        #
        self.dataLogger = DataLogger(timeformat='both')
        self.comboBox_timestamp.addItems(self.dataLogger.validTimeformats)
        self.comboBox_timestamp.setCurrentIndex(2)
        self.comboBox_timestamp.currentTextChanged.connect(self.updateLogTimeStampFormat)
        self.pushButton_startNewLog.clicked.connect(self.restartLogging)


        #
        # Crosshead
        #
        self.pushButton_corssheadMove.clicked.connect(self.moveCrosshead)
        self.doubleSpinBox_crossheadSpeed.valueChanged.connect(self.updateCrossheadSpeed)
        self.pushButton_crossheadCalibrate.clicked.connect(self.calibrateCrosshead)




        #
        # Polling thread
        #
        # self.thread = QThread()
        # self.pool = QThreadPool.globalInstance()
        # self.measValues = {'voltage': None, 'position': None, 'speed': None}






    def _connectToDevices(self):
        cMD = self.comboBox_crossheadMotor.currentText()
        lVD = self.comboBox_loadcellVoltage.currentText()
        logging.info(f"cMD: {cMD}")
        logging.info(f"lVD: {lVD}")
        if '->' in cMD:
            cMD = cMD.split('->')[1].strip()
        if not TANGO:
            self.label_connectionStatus.setText('TEST CONNECTION ESTABLISHED')
            self.pushButton_zeroVoltageCalibration.setEnabled(True)
            return
        try:
            self.crosshead.device = cMD
            self.dataLogger.addClassAttr('crossheadPosition', self.crosshead, 'position')
            # if TEST:
            #     self.dataLogger.addLogAttr('chp', cMD.rpartition("#")[0]+'/position'+'#'+cMD.rpartition("#")[2])
            # else:
            #     self.dataLogger.addLogAttr('chp', cMD+'/position')
        except:
            logging.error(f"Could not connect to device {cMD}")
        try:
            self.dataLogger.addClassAttr('loadcellVoltage', self.loadCell, 'voltage')
            self.loadCell.updateVoltageAttr(lVD)
            # self.dataLogger.addLogAttr('lcV', lVD)
        except:
            logging.error(f"Could not connect to device {lVD}")
        if self.crosshead.device is not None and self.loadCell.attrProxy is not None:
            self.label_connectionStatus.setText('CONNECTION ESTABLISHED')
        self.pushButton_zeroVoltageCalibration.setEnabled(True)
        self.dataLogger.startEv.set()
        logging.info('Setting speed value in spinbox')
        while self.crosshead is None:
            time.sleep(0.01)
        self.doubleSpinBox_crossheadSpeed.setValue(self.crosshead.speed)



        #self.PollingThread = DevicePoller(self.crossheadMotorDev, self.loadCell)
        #self.PollingThread.moveToThread(self.thread)
        #self.thread.start()


    def updateConversionEq(self):
        self.loadCell.updateType(self.comboBox_loadcell.currentText())
        self.label_conversionEq.setText(self.loadCell.getEq())
        self.lineEdit_zeroVoltageCalibration.setText(f'{self.loadCell.zeroVoltage:.3f} V')


    def calibrateZeroVoltage(self):
        self.loadCell.calibrate(self.dataLogger._lastValues['lcV'])
        self.updateConversionEq()
        self.lineEdit_zeroVoltageCalibration.setText(f'{self.loadCell.zeroVoltage:.3f} V')


    def updateTensionCompressionSign(self):
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

    def updateRStatusLabel(self):
        self.label_statusRight.setText(f"PID: {os.getpid()}, MEM (current proc): {psutil.Process().memory_info().rss/1e6:.1f} MB, CPU (system): {psutil.cpu_percent():5.1f} %")


    def updateLogTimeStampFormat(self):
        self.dataLogger.timeformat = self.comboBox_timestamp.currentText()

    def restartLogging(self):
        self.dataLogger.logfile = self.lineEdit_dataLogfile.text()


    def updateLCDNums(self):
        try:
            #logging.info(f"Updating LCD: {self.dataLogger._lastValues['chp']:.2f}")
            pos = self.dataLogger._lastValues['crossheadPosition']
            self.lcdNumber_crossheadPosition.display(f"{pos:.2f}")
            volt = self.dataLogger._lastValues['loadcellVoltage']
            self.lcdNumber_loadcellVoltage.display(f"{volt:.3f}")
            force = self.loadCell.force2(volt)
            self.lcdNumber_sampleForce.display(f"{force:.1f}")
            stress = self.sample.stress(force)
            self.lcdNumber_sampleStress.display(f"{stress:.1f}")
        except:
            pass

    def updateDevStates(self): # could only update it if the state changed, but then I'd need to store the last state of the ch
        #crosshead
        try:
            chState = self.crosshead.state
        except:
            logging.error('Could not get the crosshead motor device state')
            chState = 'UNKNOWN'

        self.pushButton_crossheadState.setStyleSheet('QPushButton {background-color: %s}' % _TangoStateColors[chState])
        self.pushButton_crossheadState.setText(str(chState))
        if chState != 'ON':
            self.pushButton_corssheadMove.setEnabled(False)
            self.doubleSpinBox_crossheadSpeed.setEnabled(False)
            self.pushButton_crossheadCalibrate.setEnabled(False)
            self.pushButton_jogNegative.setEnabled(False)
            self.pushButton_jogPositive.setEnabled(False)
        if chState == 'ON':
            if self.checkBox_crossheadEnableMove.isChecked():
                self.pushButton_corssheadMove.setEnabled(True)
            if self.checkBox_crossheadEnableCalibration.isChecked():
                self.pushButton_crossheadCalibrate.setEnabled(True)
            if self.checkBox_jogEnabled.isChecked():
                self.pushButton_jogNegative.setEnabled(True)
                self.pushButton_jogPositive.setEnabled(True)
            self.doubleSpinBox_crossheadSpeed.setEnabled(True)

    def moveCrosshead(self):
        if self.crosshead.state == 'ON':
            newPos = float(self.doubleSpinBox_crossheadMoveToPosition.value())
            self.crosshead.moveto(newPos)

    def updateCrossheadSpeed(self):
        speed = self.doubleSpinBox_crossheadSpeed.value()
        self.crosshead.speed = speed

    def calibrateCrosshead(self):
        self.crosshead.calibrate()



    def exitHandler(self):
        logging.info('exitHandler')
        if hasattr(self, 'dataLogger'):
            if not self.dataLogger.startEv.is_set():
                self.dataLogger.startEv.set()
            self.dataLogger.stopEv.set()







# Exit handler for external
def exitHandler():
    if TEST:
        logging.info('Stopping test Tango server')
        testTangoServer.terminate()
        logging.info('Stopping test Tango observer')
        testServerObserver.terminate()




def mainGUI():
    app = QtWidgets.QApplication(sys.argv)
    app.aboutToQuit.connect(exitHandler)
    main = MainWidget()
    app.aboutToQuit.connect(main.exitHandler)
    main.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    mainGUI()
