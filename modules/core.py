import time
import requests
from datetime import datetime, timedelta

from modules.configuration import dashboard_config, user_config, automation_config
from modules.database import dbEntry, db_insert
from modules.endpoints import send_push_notifications
from modules.hardware import threshold_drain, measure_waterlevel
from modules.structs import WeatherData, task


def default_process():
    
    drain_timestamp = None
    current_timestamp = None
    rainday = False
    
    while True:

        print("current task",task.current_task())

        # request Weather Data
        weatherData = WeatherData()
        print("weatherdata type:", type(weatherData))

        # skips the following code if weather data couldn't successfully initialize
        if weatherData is None:
            sleep_seconds(10)
            continue
        
        # continues if the timestamp is new (to avoid running code on the same timestamp multiple times)
        if (current_timestamp == None or weatherData.date != current_timestamp):

            # Set new Timestamp
            current_timestamp = weatherData.date
            print("current_timestamp: ", current_timestamp)

            # Get Waterlevel
            waterlevel_old = dashboard_config.waterlevel
            waterlevel_new = measure_waterlevel()
            print("new waterlevel:",waterlevel_new)

            # Set Config Values
            dashboard_config.forecast = weatherData.forecast
            dashboard_config.waterlevel = waterlevel_new
            dashboard_config.is_draining = False

            print("weatherData.projectedPPT",weatherData.projected_ppt)

            # Calculations
            waterlevel_difference = waterlevel_new - waterlevel_old
            stored_rain_volume = abs(waterlevel_difference) if waterlevel_difference > 0 else 0
            used_rain_volume = abs(waterlevel_difference) if waterlevel_difference < 0 else 0
            total_surface_area = user_config.calculate_total_surface_area()

            try:
                rain_volume = total_surface_area * weatherData.projected_ppt
            except TypeError:
                rain_volume = 0

            actual_ppt_mm = (waterlevel_difference * 1000) / total_surface_area if total_surface_area > 0 else 0
            overflow_rain_volume = rain_volume if waterlevel_new >= 0.9 and weatherData.projected_ppt > 0 else 0

            # Set Rainday
            rainday_precipitation_threshold = 2
            if weatherData.projected_ppt > rainday_precipitation_threshold:
                rainday = True
            else:
                rainday = False

            # determine drainage timestamp
            drain_timestamp = find_drain_timestamp(weatherData.forecast)
            
            print("drain_timestamp", drain_timestamp)

            if current_timestamp == drain_timestamp:
                
                if dashboard_config.control_mode:
                    #automatic mode
                    if not automation_config.user_notify:
                        task.set_task("threshold_drain",automation_config.auto_drain_amount)

                    if automation_config.drain_request:
                        automation_config.request_send = True
                        notification_message = "Tank Entwässerung empfohlen!"
                        send_push_notifications(notification_message)
                else:
                    #manual mode
                    dashboard_config.drain_advised = True
                    notification_message = "Tank Entwässerung empfohlen!"
                    send_push_notifications(notification_message)

            else:
                dashboard_config.drain_advised = False

            # Set Database Values
            dbEntry.date = weatherData.date
            dbEntry.projected_ppt = weatherData.projected_ppt
            dbEntry.actual_ppt = round(actual_ppt_mm,2)
            dbEntry.waterlevel = round(waterlevel_new,2)
            dbEntry.stored = round(stored_rain_volume,2)
            dbEntry.used = round(used_rain_volume,2)
            dbEntry.overflow = round(overflow_rain_volume,2)
            dbEntry.rainday = rainday

            # Store Data in Database
            db_insert(dbEntry)
        else:
            print("waiting")
            print("current_timestamp: ", current_timestamp)
            print("drain_timestamp", drain_timestamp)
        
        print("task drain stop: ", task.drain_stopped)

        sleep_seconds(180)

def drain_process():
    while True:
        if task.current_task() == "threshold_drain":
            print("threshold value_drain")
            threshold_drain()
        
        if task.current_task() == "default" and task.drain_stopped:
            print("drain stopped in drain process")
            task.set_drain_stopped(False)
            dashboard_config.is_draining = False

        time.sleep(5)

def timestamp_to_datetime(date_str):
    date_format = "%Y-%m-%d %H:%M"
    given_datetime = datetime.strptime(date_str, date_format)
    return given_datetime

def sleep_seconds(seconds:int):
    print("")
    print("")
    print("")
    print("")
    time.sleep(seconds)

def request_json_data(url):
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return data
    else:
        print("Failed to fetch JSON data")
        return None

def find_drain_timestamp(forecast:dict):
    values = list(forecast.values())
    keys = list(forecast.keys())

    start_index = None

    for i in range(len(values)):
        
        if values[i] != 0:
            if start_index is None:
                start_index = i

        if start_index is not None:
            
            timerange = int(int(automation_config.ppt_trigger_timerange)/5)
            end_index = int(start_index)+timerange

            cummulated_ppt_sum = sum(values[start_index:end_index])

            ppt_value_exceeds = cummulated_ppt_sum > automation_config.ppt_trigger_value
            
            if ppt_value_exceeds:
                drain_timestamp = subtract_from_timestamp(keys[start_index],automation_config.preemptive_drain_time)
                return drain_timestamp
            start_index = None

    return None

def subtract_from_timestamp(timestamp, minutes):
    format_str = "%Y-%m-%d %H:%M"
    dt = datetime.strptime(timestamp, format_str)
    new_dt = dt - timedelta(minutes=int(minutes))
    return new_dt.strftime(format_str)