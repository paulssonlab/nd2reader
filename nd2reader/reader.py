from pims.base_frames import FramesSequenceND

from nd2reader.exceptions import EmptyFileError
from nd2reader.parser import Parser
import numpy as np
import mmap
import wrapt
from copy import copy

class MemmappableFile(wrapt.ObjectProxy):
    def __init__(self, filename, memmap=False):
        self.__wrapped__ = None
        self._self_filename = filename
        self._self_memmap = memmap
        self._self_open()

    def _self_open(self):
        self._self_file = open(self._self_filename, 'rb')
        if self._self_memmap:
            wrapped = mmap.mmap(self._self_file.fileno(), 0, access=mmap.ACCESS_READ)
        else:
            wrapped = self._self_file
        super().__init__(wrapped)

    @property
    def name(self):
        return self._self_filename

    @property
    def is_memmap(self):
        return self._self_memmap

    def __reduce__(self):
        return (MemmappableFile, (self._self_filename, self._self_memmap))

    # def __getstate__(self):
    #     return {k: getattr(self, k) for k in ('_self_filename', '_self_memmap')}

    # def __setstate__(self, state):
    #     self.__dict__.update(state)
    #     self._self_open()

class ND2Reader(FramesSequenceND):
    """PIMS wrapper for the ND2 parser.
    This is the main class: use this to process your .nd2 files.
    """

    class_priority = 12

    def __init__(self, filename, memmap=False):
        super(self.__class__, self).__init__()
        self.filename = filename

        # first use the parser to parse the file
        self._fh = MemmappableFile(self.filename, memmap=memmap)
        self._parser = Parser(self._fh)

        # Set data type
        self._dtype = self._parser.get_dtype_from_metadata()

        # Setup the axes
        self._setup_axes()

        # Other properties
        self._timesteps = None

    def reopen(self):
        # TODO: this is incredibly clunky
        fh = MemmappableFile(self.filename, memmap=self._fh.is_memmap)
        reader = copy(self)
        reader._fh = fh
        parser = copy(self._parser)
        parser._fh = fh
        raw_metadata = copy(parser._raw_metadata)
        raw_metadata._fh = fh
        parser._raw_metadata = raw_metadata
        reader._parser = parser
        return reader

    @classmethod
    def class_exts(cls):
        """Let PIMS open function use this reader for opening .nd2 files

        """
        return {'nd2'} | super(ND2Reader, cls).class_exts()

    def close(self):
        """Correctly close the file handle

        """
        if self._fh is not None:
            self._fh.close()

    def _get_default(self, coord):
        try:
            return self.default_coords[coord]
        except KeyError:
            return 0

    def get_frame_2D(self, c=0, t=0, z=0, x=0, y=0, v=0, memmap=False, pims=False):
        """Gets a given frame using the parser

        Args:
            x: The x-index (pims expects this)
            y: The y-index (pims expects this)
            c: The color channel number
            t: The frame number
            z: The z stack number
            v: The field of view index

        Returns:
            numpy.ndarray: The requested frame

        """
        try:
            c_name = self.metadata["channels"][c]
        except KeyError:
            c_name = self.metadata["channels"][0]

        x = self.metadata["width"] if x <= 0 else x
        y = self.metadata["height"] if y <= 0 else y
        return self._parser.get_image_by_attributes(t, v, c_name, z, y, x, memmap=memmap, pims=pims)

    @property
    def parser(self):
        """
        Returns the parser object.
        Returns:
            Parser: the parser object
        """
        return self._parser

    @property
    def metadata(self):
        return self._parser.metadata

    @property
    def pixel_type(self):
        """Return the pixel data type

        Returns:
            dtype: the pixel data type

        """
        return self._dtype

    @property
    def timesteps(self):
        """Get the timesteps of the experiment

        Returns:
            np.ndarray: an array of times in milliseconds.

        """
        if self._timesteps is None:
            return self.get_timesteps()
        return self._timesteps

    @property
    def frame_rate(self):
        """The (average) frame rate
        
        Returns:
            float: the (average) frame rate in frames per second
        """
        total_duration = 0.0

        for loop in self.metadata['experiment']['loops']:
            total_duration += loop['duration']

        if total_duration == 0:
            raise ValueError('Total measurement duration could not be determined from loops')

        return self.metadata['num_frames'] / (total_duration/1000.0)

    def _get_metadata_property(self, key, default=None):
        if self.metadata is None:
            return default

        if key not in self.metadata:
            return default

        if self.metadata[key] is None:
            return default

        return self.metadata[key]

    def _setup_axes(self):
        """Setup the xyctz axes, iterate over t axis by default

        """
        self._init_axis_if_exists('x', self._get_metadata_property("width", default=0))
        self._init_axis_if_exists('y', self._get_metadata_property("height", default=0))
        self._init_axis_if_exists('c', len(self._get_metadata_property("channels", default=[])), min_size=2)
        self._init_axis_if_exists('t', len(self._get_metadata_property("frames", default=[])))
        self._init_axis_if_exists('z', len(self._get_metadata_property("z_levels", default=[])), min_size=2)
        self._init_axis_if_exists('v', len(self._get_metadata_property("fields_of_view", default=[])), min_size=2)

        if len(self.sizes) == 0:
            raise EmptyFileError("No axes were found for this .nd2 file.")

        # provide the default
        self.iter_axes = self._guess_default_iter_axis()

    def _init_axis_if_exists(self, axis, size, min_size=1):
        if size >= min_size:
            self._init_axis(axis, size)

    def _guess_default_iter_axis(self):
        """
        Guesses the default axis to iterate over based on axis sizes.
        Returns:
            the axis to iterate over
        """
        priority = ['t', 'z', 'c', 'v']
        found_axes = []
        for axis in priority:
            try:
                current_size = self.sizes[axis]
            except KeyError:
                continue

            if current_size > 1:
                return axis

            found_axes.append(axis)

        return found_axes[0]

    def get_timesteps(self):
        """Get the timesteps of the experiment

        Returns:
            np.ndarray: an array of times in milliseconds.

        """
        if self._timesteps is not None and len(self._timesteps) > 0:
            return self._timesteps

        self._timesteps = np.array(list(self._parser._raw_metadata.acquisition_times), dtype=np.float) * 1000.0

        return self._timesteps
