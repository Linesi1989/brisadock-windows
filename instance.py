"""One application instance per profile, with a user-only local wake-up channel."""
import hashlib
import os
from pathlib import Path
import time

from PySide6.QtCore import QObject, Signal, QLockFile
from PySide6.QtNetwork import QLocalServer, QLocalSocket

VERSION = '0.38'

class InstanceGuard(QObject):
    reveal = Signal()

    def __init__(self, directory, parent=None):
        super().__init__(parent)
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        identity = os.path.normcase(str(directory))
        self.name = 'BrisaDock-'+hashlib.sha256(identity.encode('utf-8')).hexdigest()[:28]
        self.lock = QLockFile(str(directory/'instance.lock'))
        self.lock.setStaleLockTime(0)
        self.owner = self.lock.tryLock(100)
        self.server = None
        self.error = ''
        self.peer_version = ''
        if self.owner:
            # We hold the profile lock, so a leftover endpoint belongs to a
            # process that no longer owns this profile.
            QLocalServer.removeServer(self.name)
            self.server = QLocalServer(self)
            self.server.setSocketOptions(QLocalServer.UserAccessOption)
            self.server.newConnection.connect(self.accept)
            if not self.server.listen(self.name):
                self.error = self.server.errorString()
                self.close()
        elif self.lock.error() != QLockFile.LockFailedError:
            self.error = 'No se pudo crear el bloqueo del perfil.'

    def notify(self):
        for _ in range(4):
            socket = QLocalSocket()
            socket.connectToServer(self.name)
            if socket.waitForConnected(250):
                socket.write(b'show\n')
                socket.waitForBytesWritten(250)
                if socket.bytesAvailable() or socket.waitForReadyRead(750):
                    self.peer_version = bytes(socket.readAll()).decode('ascii', errors='ignore').strip()
                socket.disconnectFromServer()
                return True
            time.sleep(.05)
        return False

    def accept(self):
        while self.server and self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.disconnected.connect(socket.deleteLater)
            def receive(connection=socket):
                if connection.bytesAvailable() > 32:
                    connection.disconnectFromServer()
                    return
                if not connection.canReadLine():
                    return
                data = bytes(connection.readLine(32))
                if data == b'show\n':
                    connection.write((VERSION+'\n').encode('ascii'))
                    connection.flush()
                    self.reveal.emit()
                connection.disconnectFromServer()
            socket.readyRead.connect(receive)
            if socket.bytesAvailable():
                receive()

    def close(self):
        if self.server:
            self.server.close()
        if self.owner:
            self.lock.unlock()
            self.owner = False
