from collections import Iterable
from math import sqrt, isfinite
import numpy as np
from geoproj.proj import TransverseMercator
from gnss_timeseries.gnss_timeseries import (GnssTimeSeries, parse_time,
                                             parse_frequency)
from gnss_timeseries.aux import default_win_ref, default_win_pgd
# maximum distance (km) to the hipocenter for Mw estimation using PGD
default_max_distance = 800.


class NetworkTimeSeries:
    """
    This class stores data of GNSS stations using GnssTimeSeriesSimple
    instances

    :ivar _station_ts: list of instances of
        :py:class:`gnss_timeseries.gnss_timeseries.GnssTimeSeriesSimple`
    """

    def __init__(self, length='1h', sampling_rate='1/s',
                 window_offset='7m', **kwargs_other):
        self.n_sta = 0
        self._station_ts = []
        self._ref_coords = []
        self._codes = []
        self._code2index = dict()
        self._names = []
        self.window_offset = parse_time(window_offset)
        self.ts_length = parse_time(length)
        self.s_rate = parse_frequency(sampling_rate)
        self._kwargs_other = kwargs_other
        self._tm = TransverseMercator(0., 0.)
        self._lat_range = np.array([100., -100.])
        self._lon_range = np.array([370., -200.])
        self._lon_ref = np.nan
        # hypocentral parameters
        self._hypocenter = np.full(3, np.nan)  # lon, lat, depth
        self._t_origin = np.nan  # origin time (UTC timestamp)
        self._distance_dict = dict()
        self._max_distance = np.nan

    def available_window(self):
        if self.n_sta == 0:
            return np.nan, np.nan, np.nan
        t_min = np.inf
        t_max = -np.inf
        t_oldest = np.inf
        for index in range(self.n_sta):
            ts = self._station_ts[index]
            if ts.t_first < t_min:
                t_min = ts.t_first
            if ts.t_last > t_max:
                t_max = ts.t_last
            if ts.t_oldest < t_oldest:
                t_oldest = ts.t_oldest
        return t_min, t_oldest, t_max

    def available_window_contains(self, t):
        t_min, t_oldest, t_max = self.available_window()
        return t_oldest < t < t_max

    def set_window_offset(self, window_offset):
        self.window_offset = window_offset
        for ts in self._station_ts:
            ts.set_window_offset(window_offset)

    def station_timeseries(self, sta):
        """Buffer of the a station (GnssTimeSeriesSimple)

        :param sta: station index or code
        :return: buffer
        """
        return self._station_ts[self._sta2index(sta)]

    def add_station(self, code, ref_coords=None, name=''):
        """New station in the buffer

        :param code: station's 4 letter code
        :param ref_coords: (longitude, latitude)
        :param name: station's name
        """
        if ref_coords is None:
            print('\n'*2)
            print('  *** {:s}:  no ref coords.'.format(code))
            print('\n'*2)
            return
        self._code2index[code] = self.n_sta
        self.n_sta += 1
        self._codes.append(code)
        self._names.append(name)
        self._ref_coords.append(
            (np.nan, np.nan) if ref_coords is None else ref_coords)
        self._station_ts.append(GnssTimeSeries(
            length=self.ts_length, sampling_rate=self.s_rate,
            window_offset=self.window_offset))
        if ref_coords is not None:
            lat = ref_coords[1]
            if lat < self._lat_range[0]:
                self._lat_range[0] = lat
            elif lat > self._lat_range[1]:
                self._lat_range[1] = lat
            lon = ref_coords[0]
            if lon < self._lon_range[0]:
                self._lon_range[0] = lon
            elif lon > self._lon_range[1]:
                self._lon_range[1] = lon

    def lat_range(self):
        return self._lat_range

    def lon_range(self):
        return self._lon_range

    def clear_data_at(self, sta):
        """Clears the data at one station

        :param sta: station index or code
        """
        self.station_timeseries(sta).clear()

    def clear_all_data(self):
        for ts in self._station_ts:
            ts.clear()

    def station_buffer_is_empty(self, sta_code):
        return self.station_timeseries(sta_code).cleared

    def station_is_available(self, sta_code):
        return sta_code in self._codes

    def get_coords(self, sta, get_time=True, layers=None, as_dict=False):
        return self.station_timeseries(sta).get(
            layers=layers, as_dict=as_dict, get_time=get_time)

    def get_coords_near(self, sta, t, layers=None, as_dict=False):
        return self.station_timeseries(sta).get_point(
            t, layers=layers, as_dict=as_dict)

    def get_interval(self, sta, t_begin, t_end, layers=None,
                     as_dict=False, get_time=True):
        return self.station_timeseries(sta).interval(
            t_begin, t_end, layers=layers, as_dict=as_dict, get_time=get_time)

    def get_last(self, sta, t_window, layers=None,
                 as_dict=False, get_time=True):
        return self.station_timeseries(sta).last(
            t_window, layers=layers, as_dict=as_dict, get_time=get_time)

    def get_first(self, sta, t_window, layers=None,
                  as_dict=False, get_time=True):
        return self.station_timeseries(sta).first(
            t_window, layers=layers, as_dict=as_dict, get_time=get_time)

    def get_layers(self, sta, layers, get_time=True, as_dict=False):
        return self.station_timeseries(sta).get(
            layers=layers, get_time=get_time, as_dict=as_dict)

    def add_point_to_station(self, sta, coords, std_coords, t, **kwargs):
        self.station_timeseries(sta).add_point(
            dict(coords=coords, std_coords=std_coords), t, **kwargs)

    def set_station_timeseries(self, sta, coords, std_coords, t,
                               check_sampling_rate=False):
        sta_timeseries = self.station_timeseries(sta)
        if check_sampling_rate:
            beta = np.median(t[1:] - t[:-1])*sta_timeseries.s_rate
            if abs(beta - 1.0) < 1.e-8:
                coords_aux = coords
                std_coords_aux = std_coords
            else:
                n = t.size
                m = int(round(beta))
                n_aux = (n-1)*m + 1
                coords_aux = []
                std_coords_aux = []
                for k in range(3):
                    x = np.full(n_aux, np.nan)
                    x[::m] = coords[k]
                    coords_aux.append(x)
                    s = np.full(n_aux, np.nan)
                    s[::m] = std_coords[k]
                    std_coords_aux.append(s)
        else:
            coords_aux = coords
            std_coords_aux = std_coords

        self.station_timeseries(sta).set_series(
            {'coords': coords_aux, 'std_coords': std_coords_aux}, t[-1])

    def ref_coords(self, code=None):
        """Reference coordinates of a station

        :param code: station index or code
        :return: longitude, latitude
        """
        if code is None:
            return self._ref_coords
        elif isinstance(code, str):
            return self._ref_coords[self._sta2index(code)]
        else:
            return tuple(self._ref_coords[self._sta2index(c)] for c in code)

    def dist_to_hypocenter(self, code=None):
        if code is None:
            return self._distance_dict
        return self._distance_dict.get(code, None)

    def hypocenter(self):
        return self._hypocenter

    def t_origin(self):
        return self._t_origin

    def max_distance(self):
        return self._max_distance

    def ref_values_at(self, sta_code):
        return self.station_timeseries(sta_code).ref_values()

    def ref_values_are_set_at(self, sta_code):
        return self.station_timeseries(sta_code).ref_values_are_set()

    def ref_coord_vectors(self, stations=None):
        if stations is None:
            n_sta = self.n_sta
            stations = self._codes
        else:
            n_sta = len(stations)
        lon = np.zeros(n_sta)
        lat = np.zeros(n_sta)
        if stations is None:
            for k, x in enumerate(self._ref_coords):
                lon[k], lat[k] = x
        else:
            for k, code in enumerate(stations):
                index = self._code2index[code]
                lon[k], lat[k] = self._ref_coords[index]
        return (lon, lat), stations

    def station_codes(self):
        return self._codes

    def station_code(self, index):
        return self._codes[index]

    def station_name(self, code):
        return self._names[self._sta2index(code)]

    def station_index(self, code):
        return self._code2index[code]

    def _sta2index(self, code):
        if isinstance(code, int):
            return code
        return self._code2index[code]

    def get_indices(self, codes):
        return [self._code2index[code] for code in codes]

    def set_t_origin(self, t_origin):
        if t_origin is not None:
            self._t_origin = t_origin

    def set_hypocenter_coords(self, coords, max_distance=None):
        if coords is None or not np.any(np.isnan(self._hypocenter)) and max(
                abs(coords[0] - self._hypocenter[0]),
                abs(coords[1] - self._hypocenter[1]),
                0.01*abs(coords[2] - self._hypocenter[2])) < 1.e-5:
            return
        if max_distance is None:
            max_distance = default_max_distance
        self._max_distance = max_distance
        self._hypocenter = coords
        self._tm.reset(coords[0], coords[1])
        depth_2 = coords[2]*coords[2]
        for code, ref_coords in zip(self._codes, self._ref_coords):
            if ref_coords is None:
                continue
            x, y = self._tm(*ref_coords)
            r = sqrt(depth_2 + (x*x + y*y)*1.e-6)
            if r < max_distance:
                self._distance_dict[code] = r
            else:
                self._distance_dict.pop(code, None)

    def eval_ref_values(self, t_origin=None, window_ref=default_win_ref,
                        force_eval_ref_values=False, **kwargs_mean):
        self.set_t_origin(t_origin)
        for ts in self._station_ts:
            ts.eval_ref_values(self._t_origin, window_ref=window_ref,
                               force_eval_ref_values=force_eval_ref_values,
                               **kwargs_mean)

    def eval_pgd(self, t_s_dict=None,
                 window_pgd=default_win_pgd, only_hor=False,
                 window_ref=default_win_ref, force_eval_ref_values=False,
                 **kwargs_mean):
        pgd_dict = dict()
        if t_s_dict is None:
            t_s_dict = dict()
        for code in self._codes:
            pgd_dict[code] = self.eval_pgd_at_station(
                code, t_s=t_s_dict.get(code), window_pgd=window_pgd,
                only_hor=only_hor, window_ref=window_ref,
                force_eval_ref_values=force_eval_ref_values, **kwargs_mean)
        return pgd_dict

    def eval_offset(self, t_eval_dict, window_ref=default_win_ref,
                    force_eval_ref_values=False, **kwargs_mean):
        offset_dict = dict()
        for code in self._codes:
            offset_dict[code] = self.eval_offset_at_station(
                code, t_eval_dict.get(code), window_ref=window_ref,
                force_eval_ref_values=force_eval_ref_values, **kwargs_mean)
        return offset_dict

    def eval_pgd_at_station(self, code, t_s=None,
                            window_pgd=default_win_pgd,
                            only_hor=False, window_ref=default_win_ref,
                            force_eval_ref_values=False, **kwargs_mean):
        return self._station_ts[self._code2index[code]].eval_pgd(
            self._t_origin, t_s=t_s, window_pgd=window_pgd, only_hor=only_hor,
            window_ref=window_ref, force_eval_ref_values=force_eval_ref_values,
            **kwargs_mean)

    def eval_offset_at_station(self, code, t_eval, window_ref=default_win_ref,
                               force_eval_ref_values=False, **kwargs_mean):
        return self._station_ts[self._code2index[code]].eval_offset(
            t_eval=t_eval, t_origin=self._t_origin, window_ref=window_ref,
            force_eval_ref_values=force_eval_ref_values, **kwargs_mean)

    def eval_pgd_and_mw(self, t_s_dict=None, window_pgd=default_win_pgd,
                        only_hor=False, **kwargs_ref_value):
        aux = self.eval_pgd(t_s_dict=t_s_dict, window_pgd=window_pgd,
                            only_hor=only_hor, **kwargs_ref_value)
        return self.mw_from_pgd(
            {code: value['PGD'] for code, value in aux.items()})

    def mw_from_pgd(self, pgd_dict):
        results_dict = dict()
        for code, r in self._distance_dict.items():
            try:
                pgd = pgd_dict[code]
                if isfinite(pgd):
                    results_dict[code] = (mw_melgar(100*pgd, r), r)
            except KeyError:
                pass
        return results_dict

    def mw_timeseries_from_pgd(self, vel_mask=3., sta_list=None, window=300,
                               max_distance=None, **kwargs_ref_value):
        pgd_dict = self.pgd_timeseries(sta_list=sta_list,
                                       window=window, **kwargs_ref_value)
        # times at which each station is reached by the mask

        t_mask = []
        codes = []
        if max_distance is None:
            max_distance = default_max_distance
        for code, r in self._distance_dict.items():
            if r > max_distance or pgd_dict[code][0] is None:
                continue
            t_mask.append(self._t_origin + r/vel_mask)
            codes.append(code)
        if len(codes) == 0:
            return None, None
        t_mask = np.array(t_mask)
        # minimum and maximum times
        t_min = np.inf
        t_max = -np.inf
        for code in codes:
            t = pgd_dict[code][1]
            aux = t[0]
            if aux < t_min:
                t_min = aux
            aux = t[-1]
            if aux > t_max:
                t_max = aux
        t_step = 1./self.s_rate
        t_mw = np.arange(t_min-self._t_origin,
                         t_max-self._t_origin+0.5*t_step,
                         t_step)
        mw = np.zeros(t_mw.size)
        count = np.zeros(t_mw.size, dtype=int)

        for code, t_m in zip(codes, t_mask):
            pgd, t = pgd_dict[code]
            if t_m > t[-1]:
                continue
            k = np.argmin(np.abs(t-t_m))
            aux = mw_melgar(100*pgd[k:], self._distance_dict[code])
            i1 = np.argmin(np.abs(t_mw - (t[k] - self._t_origin)))
            i2 = i1 + aux.size
            mw[i1:i2] += aux
            count[i1:i2] += 1
        count[count == 0] = 1
        mw /= count
        mw[mw < 1.e-5] = np.nan
        return mw, t_mw

    def pgd_timeseries(self, sta_list=None,
                       window=300, **kwargs_ref_value):
        if sta_list is None:
            sta_list = self.station_codes()
        pgd_dict = dict()
        for code in sta_list:
            pgd_dict[code] = self.station_timeseries(
                code).pgd_timeseries(self._t_origin, window=window,
                                     **kwargs_ref_value)
        return pgd_dict

    def ground_displ_timeseries(self, sta_list=None,
                                window=300, **kwargs_ref_value):
        if sta_list is None:
            sta_list = self.station_codes()
        ground_displ_dict = dict()
        for code in sta_list:
            ground_displ_dict[code] = self.station_timeseries(
                code).ground_displ_timeseries(self._t_origin, window=window,
                                              **kwargs_ref_value)
        return ground_displ_dict

    def set_win_offset(self, win_offset):
        win = parse_time(win_offset)
        for ts in self._station_ts:
            ts.win_offset = win


def mw_crowel(pgd, r_hypo):
    r"""Computes an estimate of the moment magnitude of an earthquake as a
    given the peak ground displacement (PGD) and the hypocentral distance,
    as proposed by Crowel *et. al* (2013)

    .. math::
        \log(\text{PGD}) = -5.013 + 1.219\,M_W - 0.178\,M_W \log(R)

    Crowel *et. al* (2013)

    *Earthquake magnitude scaling using seismogeodetic data*

    *Geophys. Res. Lett., 40, 6089–6094*

    .. Article:  https://agupubs.onlinelibrary.wiley.com/doi/full/
        10.1002/2013GL058391

    :param pgd: peak ground displacement :math:`\text{PGD}` in cm
    :param r_hypo: distance to hypocenter in km :math:`R`.
    :return: estimate of the moment magnitude :math:`M_W`
    """
    return (np.log(pgd) + 5.013)/(1.219 - 0.178*np.log(r_hypo))


def mw_melgar(pgd, r_hypo):
    r"""Computes an estimate of the moment magnitude of an earthquake as a
    given the peak ground displacement (PGD) and the hypocentral distance,
    as proposed by D. Melgar *et. al* (2015).

    .. math::
        \log(\text{PGD}) = -4.434 + 1.047\,M_W - 0.138\,M_W \log(R)

    Melgar *et. al* (2015)

    *Earthquake magnitude calculation without saturation from the scaling of
    peak ground displacement.*

    *Geophys. Res. Lett., 42, 5197–5205*

    .. Article:  https://agupubs.onlinelibrary.wiley.com/doi/full/
        10.1002/2013GL058391

    :param pgd: peak ground displacement :math:`\text{PGD}` in cm
    :param r_hypo: distance to hypocenter in km :math:`R`.
    :return: estimate of the moment magnitude :math:`M_W`
    """
    return (np.log10(pgd) + 4.434)/(1.047 - 0.138*np.log10(r_hypo))
