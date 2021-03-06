"""
    arensync -- backup and restore using secure (ssh and gpg) methods
    Copyright 2017 Pavel Pletenev <cpp.create@gmail.com>
    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
# pylint: disable=C0111,C0112,C1801,W0201,C0330,no-member,invalid-name
import os
import hashlib
# from pprint import pprint
from operator import itemgetter as by
from itertools import groupby  # , chain
from fnmatch import fnmatch
import tempfile
import time

from tqdm import tqdm
from plumbum import local, colors, FG
from . import ConfiguredApplication, N_


def blockreader(f, block_size=4096):
    while True:
        b = f.read(block_size)
        if len(b) == 0:
            return
        yield b


def hash_file(p, hashclass=hashlib.sha256):
    p, lp = p
    with open(p, 'rb') as f:
        hashobj = hashclass()
        for block in blockreader(f):
            hashobj.update(block)
    return dict(hash=hashobj.hexdigest(), file=lp)


def uuniq(arr, key):
    y = [list(y) for x, y in groupby(arr, key)]
    return [x[0] for x in y if len(x) == 1]


def uniq(arr, key):
    return [next(y) for x, y in groupby(arr, key)]


def non_repetative(y):
    y = list(y)
    if len(y) == 0:
        return y[0]


def diff_files(arr1, arr2):
    # temp = sorted(chain(arr1, arr2, arr2), key=by('file'))
    # return uuniq(temp, by('file'))
    return [x for x in arr1 if x not in arr2]


class arensync(ConfiguredApplication):
    def filter_ignored(self, fl):
        if self.ignored is None:
            return fl
        a = []
        for f, ff in fl:
            for i in self.ignored:
                if fnmatch(f, i):
                    continue
            a.append((f, ff))
        return a

    def get_server_files(self):
        if len(self.remote['ls'](self.serverdir)[:-1].split('\n')) <= 1:
            serverfiles = []
        else:
            serverfiles = [
                {'hash': line[0:64].rstrip(' '), 'file': line[65:].lstrip(' ')}
                for lst in sorted(
                    tqdm(self.serverdir // '*.lst'),
                    reverse=True
                ) for line in self.remcat(lst).split('\n')
                if line != ''
            ]
            serverfiles = uniq(sorted(serverfiles, key=by('file')), by('file'))
        return serverfiles

    def get_local_files(self):
        localfiles = []
        for fdir, _, files in tqdm(os.walk(self.workdir)):
            ldir = fdir.replace(self.workdir, '.')
            nonignored = list(self.filter_ignored(
                (os.path.join(fdir, f), os.path.join(ldir, f))
                for f in files))
            localfiles.extend(self.pool.map(hash_file, nonignored))
        return localfiles

    def algorithm(self):
        print(N_("Finding changed files and uploading to server"))  # noqa: Q000
        serverfiles = self.get_server_files()
        localfiles = self.get_local_files()
        to_upload = diff_files(localfiles, serverfiles)
        if len(to_upload) == 0:
            print(colors.red | N_("Files unchanged. Nothing new to upload."))  # noqa: Q000
            return
        package_size = 0
        packages = [list()]
        for x in to_upload:
            packages[-1].append(x)
            package_size += local.path(self.workdir / x['file']).stat().st_size
            if package_size >= self.max_package_size:
                package_size = 0
                packages.append(list())
            print(colors.green | x['file'])
        archive = local['date']['+archive%Y%m%d_%H%M%S_N{index}.tar.gz']()[:-1]
        for package_index, to_upload in enumerate(packages):
            self.do_arensync(archive, package_index, to_upload)

    def do_arensync(self, archive, package_index, to_upload):
        archive_name = archive.format(index=package_index)
        print(colors.green | archive_name)
        archivepath = self.tempdir / archive_name
        archivelist = self.tempdir / (archive_name + '.lst')
        localarchivegpg = archive_name + '.gpg'
        archivegpg = self.tempdir / localarchivegpg
        archivegpgsum = self.tempdir / (archive_name + '.gpg.sum')
        with open(archivelist, 'w') as f:
            f.write('\n'.join([
                '{hash} {file}'.format(**x) for x in to_upload]))
        with tempfile.NamedTemporaryFile(mode='w') as f:
            f.write('\n'.join(map(by('file'), to_upload)))
            f.flush()
            t = self.tar['cvzf', archivepath, '-C', self.workdir, '-T', f.name]
            t & FG()  # pylint: disable=W0106
        self.encrypt(archivepath)
        with local.cwd(self.tempdir):
            (local['sha256sum'][localarchivegpg] > archivegpgsum)()
        archivepath.delete()
        self.remote.upload(archivelist, self.serverdir)
        self.remote.upload(archivegpg, self.serverdir)
        self.remote.upload(archivegpgsum, self.serverdir)
        with self.remote.cwd(self.serverdir):
            self.remote['sha256sum']('-c', archive_name + '.gpg.sum')
        archivelist.delete()
        archivegpg.delete()
        archivegpgsum.delete()


def main():
    arensync.run()
