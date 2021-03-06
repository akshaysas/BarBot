import RPi.GPIO as GPIO
import time
import tkinter as tk
import traceback
import threading
from recipe import upload_recipe, get_recipe, get_all_recipes
from utils import name_to_upper
from cocktailStats import increment_cocktail
import json
import subprocess
from datetime import datetime

#This is the class where BarBot's primary functionality is defined
class Main():

    #Initialize all class variables
    def __init__(self):
        self.polarity_pins = []
        self.pressure_pins = []
        self.abort_pins = [] #In, out
        self.polarity_normal = True
        self.cocktail_ingredients = {}
        self.cocktail_amounts = {}
        self.cocktail_buttons = {}
        self.cocktail_available = {}
        self.ignore_list = set()
        self.alcohol_list = set()
        self.alcohol_mode = False
        self.new_bottles = set()
        self.pump_map = {}
        self.pump_data = {}
        self.cocktail_count = 0
        self.clean_time = 8  #Regular Time: 12 seconds
        self.shot_volume = 44.36 #mL
        self.busy_flag = False
        self.window = None
        self.start_time = 0.0 #Time that pumps started
        self.abort_time = 0.0 #Time that abort was triggered
        self.current_cocktail = '' #Name of cocktail being made

        #Configure hardware and load data from cloud & local config files
        self.load_settings() #Load settings file
        self.load_pump_config() #Load configuration of pumpMap and pumpData
        self.setup_pins() #Setup GPIO pins
        self.load_new_bottles() #Load bottle list from local file
        self.load_alcohol_list() #Load list of ingredients listed as alcohol
        self.load_ignore_list() #Load list of ingredients to be ignored in determining menu
        self.update_local_recipes() #Updates local recipes to match cloud; loads recipes locally; checks cocktail availablity


    #Sets up pins by setting gpio mode and setting initial output
    def setup_pins(self):
        try:
            print("Setting up pump pins...")
            GPIO.setmode(GPIO.BCM)

            #Set all peristaltic pump relay pins to HIGH (turns pumps off)
            for pump in self.pump_data:
                GPIO.setup(self.pump_data[pump]['gpio'], GPIO.OUT)
                GPIO.output(self.pump_data[pump]['gpio'], GPIO.HIGH)

            #Turn off signal for #1 relay
            GPIO.setup(self.polarity_pins[0], GPIO.OUT)
            GPIO.output(self.polarity_pins[0], GPIO.LOW)

            # Turn on signal for #2 relay
            GPIO.setup(self.polarity_pins[1], GPIO.OUT)
            GPIO.output(self.polarity_pins[1], GPIO.HIGH)

            #Setup pressure pins
            for pump in self.pressure_pins:
                GPIO.setup(self.pressure_pins[pump], GPIO.OUT)
                GPIO.output(self.pressure_pins[pump], GPIO.HIGH)

            #Setup abort pins
            #GPIO.setup(self.abort_pins[0], GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            #GPIO.add_event_detect(self.abort_pins[0], GPIO.RISING, callback=self.abort_pumps, bouncetime=200)

            print("Pins successfully setup!")
        except Exception as e:
            print("Error setting up pump pins: " + str(e))
            exit(1)


    #Load pump configuration
    def load_pump_config(self):
        data = []
        with open('pumpConfig.json', 'r') as file:
            data = json.load(file)

        #Save data into class variable
        for pump in data:
            self.pump_data[pump['pumpNum']] = {
                "pumpNum": pump['pumpNum'],
                "gpio": pump['gpio'],
                "type": pump['type'],
                "pumpTime": pump['pumpTime']
            }

            #Make sure there is a bottle on the pump before adding to pump_map
            if(pump['currentBottle'] != {}):
                self.pump_map[pump['currentBottle']['name']] = {
                    "name": pump['currentBottle']['name'],
                    "volume": pump['currentBottle']['volume'],
                    "originalVolume": pump['currentBottle']['originalVolume'],
                    "pumpNum": pump['pumpNum'] #NOTE: Should be removed before writing back to file
                }

    #Loads settings from file
    def load_settings(self):
        data = {}
        with open('settings.json', 'r') as file:
            data = json.load(file)
        
        self.polarity_pins = data['polarityPins']
        self.pressure_pins = data['pressurePins']
        self.abort_pins = data['abortPins']


    #Test function that runs all of the pumps for 3 seconds each
    def test_pumps(self):
        try:
            for pump in self.pump_data:
                GPIO.output(self.pump_data[pump]['gpio'], GPIO.LOW)
                print("Turning on pin " + str(pump['gpio']))
                time.sleep(3)
                GPIO.output(self.pump_data[pump]['gpio'], GPIO.HIGH)
                time.sleep(1)
        except KeyboardInterrupt:
            print('Exitting early')
            GPIO.cleanup()
            exit()

    #Load cocktails from local recipe cache file
    def load_cocktails(self):
        data = {}
        with open('cocktails.json', 'r') as file:
            data = json.load(file)

        self.cocktail_available = {}
        self.cocktail_amounts = {}
        self.cocktail_ingredients = {}
        i = 0
        #Loads all cocktail details into separate python objects
        for cocktails in data['cocktails']:
            cocktail_name = str(data['cocktails'][i]['name'])
            self.cocktail_ingredients[cocktail_name] = data['cocktails'][i]['ingredients']
            self.cocktail_amounts[cocktail_name] = data['cocktails'][i]['amounts']
            self.cocktail_available[cocktail_name] = self.is_available(cocktail_name)
            print(cocktail_name + " available: " + str(self.cocktail_available[cocktail_name]))
            i = i+1
        self.cocktail_count = i


    #Aborts all pump functions
    def abort_pumps(self, channel):
        print('ABORTING ALL FUNCTIONS')
        self.abort_time = datetime.now().timestamp()
        #Turns off all pumps and solenoids
        for pump_num in self.pump_data:
            pin = self.pump_data[pump_num]['gpio']
            GPIO.output(pin, GPIO.HIGH)
        
        #Turns of all pressure pumps
        for press_pin in self.pressure_pins:
            GPIO.output(pin, GPIO.HIGH)

        #Fix all volume adjustments that were made
        self.abort_fix_volumes()

    #Fix volume adjustments that were made since the cocktail was aborted
    def abort_fix_volumes(self):
        
        time_spent = self.abort_time - self.start_time
        i = 0
        for ingredient in self.cocktail_ingredients[self.current_cocktail]:
            amount_desired = self.cocktail_amounts[self.current_cocktail][i] #Num of shots desired
            pump_num = self.pump_map[ingredient]['pumpNum']
            time_expected = amount_desired * self.pump_data[pump_num]['pumpTime']

            #Nothing to change
            if(time_expected <= time_spent):
                i += 1
                continue
            
            amount_dispensed = (time_spent / self.pump_data[pump_num]['pumpTime'])*self.shot_volume  #Total mL dispensed
            amount_diff = amount_desired*self.shot_volume - amount_dispensed #Calculate amount not dispensed
            self.pump_map[ingredient]['volume'] = str(float(self.pump_map[ingredient]['volume']) + amount_diff) #Add to amount stored in file
            i += 1
        self.write_pump_data()

    #Loads the list of ingredients to ignore when considering availablity & making cocktail
    def load_ignore_list(self):
        data = None

        with open('ignoreList.json', 'r') as file:
            data = json.load(file)
        
        self.ignore_list = set(data)


    #Write ignore list to file
    def write_ignore_list(self):
        ignore_arr = list(self.ignore_list)

        with open('ignoreList.json', 'w') as file:
            json.dump(ignore_arr, file)

        print('Updated ignore list file')


    #Loads the list of ingredients that are alcohol
    def load_alcohol_list(self):
        data = {}

        with open('alcohol.json', 'r') as file:
            data = json.load(file)
        
        for key in data:
            if(data[key] == True):
                self.alcohol_list.add(key)

    #Adds item to ignore list
    def add_ignore_item(self, item):
        print('Adding: ' + item + ' to ignore list!')
        self.ignore_list.add(item)
        self.write_ignore_list() #Update local storage
        self.load_cocktails()  #Reload cocktails with new ignored ingredients

    #Removes item from ignore list
    def remove_ignore_item(self, item):
        if(item in self.ignore_list):
            print('Removing ' + item + ' from ignore list!')
            self.ignore_list.remove(item)
            self.write_ignore_list()  #Updates local storage file
            self.load_cocktails()  #Reload cocktail list

    #Get ignore ingredient list
    def get_ignore_ingredients(self):
        return list(self.ignore_list)

    
    #Writes alcohol list to file
    def write_alcohol_list(self):

        data = {}
        all_bottles = self.new_bottles

        #Add bottles that are currently on pumps
        for bottle in self.pump_map.keys():
            all_bottles.add(bottle)

        #Go through all ingredients and construct object
        for ingredient in all_bottles:
            #print(ingredient + ": " + str(ingredient in self.alcoholList))
            if(ingredient in self.alcohol_list):
                data[ingredient] = True
            else:
                data[ingredient] = False

        with open('alcohol.json', 'w') as file:
            json.dump(data, file)

        print('Updated alcohol list file')

    
    #Add a bottle to alcohol list
    def add_to_alcohol_list(self, bottle_name):
        self.alcohol_list.add(bottle_name)
        self.write_alcohol_list()

    #Get number/details of bottles supported by Barbot
    def get_pump_support_details(self):
        pump_arr = []

        #Get details of every pump
        for num in self.pump_data:
            pump_obj = {
                "pumpNum": num,
                "pumpTime": self.pump_data[num]['pumpTime'],
                "type": self.pump_data[num]['type']
            }
            pump_arr.append(pump_obj)

        pump_arr.sort(key= lambda e: e['pumpNum']) #Make sure result is sorted by pumpNum

        return pump_arr
    
    #Add cocktail recipe to BarBot-Recipes Table in DynamoDB
    def add_cocktail_recipe(self, recipe):
        if(upload_recipe(recipe)):
            #Updates the local recipe cache json file
            res = self.update_local_recipes()
            if(res == False):
                return 'false'
            return 'true'
        return 'false'

    #Updates cocktails.json with data from the Dynamodb table
    def update_local_recipes(self):
        new_recipe_raw = get_all_recipes()

        if(new_recipe_raw == {}):
            print('Error getting recipes from DynamoDB')
            self.load_cocktails()
            return False

        new_cocktail_JSON = {'cocktails': []}

        for rec in new_recipe_raw:
            amount_item = json.loads(new_recipe_raw[rec])['amounts']
            
            ingredient_arr = []
            amounts_arr = []
            for ingredient in amount_item:
                ingredient_arr.append(ingredient)
                amounts_arr.append(amount_item[ingredient])
            
            new_itew = {
                "name": rec,
                "ingredients": ingredient_arr,
                "amounts": amounts_arr
            }

            new_cocktail_JSON['cocktails'].append(new_itew)

        with open('cocktails.json', 'w') as file:
            json.dump(new_cocktail_JSON, file)

        print('Wrote new cocktails to file')
        self.load_cocktails()

        return True


    #Scans through the ingredients on each pump and the ingredients needed for this cocktail to determine availability
    def is_available(self, cocktail_name):    
        if(not self.alcohol_mode):
            for ingredient in self.cocktail_ingredients[cocktail_name]:
                if(ingredient not in self.pump_map.keys() and ingredient not in self.ignore_list):
                    print(ingredient + " not available!")
                    return False
                elif(ingredient in self.ignore_list):
                    print("CAN IGNORE INGREDIENT: " + ingredient + '  FOR COCKTAIL: ' + cocktail_name)
            return True
        else:
            alc_count = 0
            for ingredient in self.cocktail_ingredients[cocktail_name]:
                if(ingredient in self.alcohol_list):
                    alc_count += 1
                    if(ingredient not in self.pump_map.keys() and ingredient not in self.ignore_list):
                        print(ingredient + " not available!")
                        return False
            
            #Make sure it's not a non-alcoholic drink
            if(alc_count == 0):
                return False
                
            return True

    
    #Load new bottles
    def load_new_bottles(self):
        with open('bottles.json', 'r') as file:
            data = json.load(file)

        self.new_bottles = set(data)
        print('NEW BOTTLES:')
        print(self.new_bottles)


    #Write bottles list to the bottles.json file
    def write_new_bottles(self):
        with open('bottles.json', 'w') as file:
            json.dump(list(self.new_bottles), file)

    
    #Adds new bottle to the bottle list
    def add_new_bottle_to_list(self, bottle_name):
        print("ADDING " + bottle_name + " TO BOTTLE LIST")
        if(bottle_name.lower() not in self.new_bottles):
            self.new_bottles.add(bottle_name.lower())
            self.write_new_bottles()
        else:
            print('Bottle: ' + bottle_name + "  is already in the list")


    #Removes bottle from bottle list
    def remove_bottle_from_list(self, bottle_name):
        if(bottle_name in self.new_bottles):
            self.new_bottles.remove(bottle_name)
            self.write_new_bottles()
        else:
            print("Bottle: " + bottle_name + "  not in list to begin with!")


    #Function that crafts the cocktail requested
    def make_cocktail(self, cocktail_name):
        if(self.busy_flag):
            print('Busy making cocktail!')
            return 'busy'
        
        #Check whether the cocktail is available or not
        if(not self.cocktail_available[cocktail_name]):
            print('This cocktail is not avialable!')
            return 'available'
        
        #Check whether there are enough ingredients
        if(not self.can_make_cocktail(cocktail_name)):
            print('Not enough ingredients to make this cocktail.')
            return 'ingredients'
        
        try:
            print('Making cocktail ' + cocktail_name)
            self.busy_flag = True
            #self.setup_pins()

            #Now we need to turn on pumps for respective ingredients for specified times
            i = 0
            wait_time = 0
            biggest_time = 0
            self.current_cocktail = cocktail_name
            self.start_time = datetime.now().timestamp()
            for ingredient in self.cocktail_ingredients[cocktail_name]:
                #Skip pumping non-alcohol ingredients
                if(self.alcohol_mode and ingredient not in self.alcohol_list):
                    print(ingredient + ' is not alcohol. Skipping to next ingredient...')
                    i += 1
                    continue

                if(ingredient in self.ignore_list):
                    print(ingredient + ' is in ignore list. Skipping to next ingredient...')
                    i += 1
                    continue

                print('Starting pump for ingredient: ' + ingredient)

                pump_num = self.pump_map[ingredient]['pumpNum']
                #Create threads to handle running the pumps
                pump_thread = threading.Thread(target=self.pump_toggle, args=[pump_num, self.cocktail_amounts[cocktail_name][i]])
                pump_thread.start()

                #Determine if pressure pumps should be triggered
                if(self.pump_data[pump_num]['type'] == 'soda'):
                    percent = float(self.get_bottle_percentage(ingredient))/100
                    pressure_time = self.cocktail_amounts[cocktail_name][i] * self.pump_data[pump_num]['pumpTime'] * 0.75  #pressure pump time in seconds

                    pressure_thread = threading.Thread(target=self.pressure_toggle, args=[pump_num, pressure_time])
                    pressure_thread.start()

                #Adjust volume tracking for each of the pumps
                print('Ingredient: ' + str(ingredient) + ' --- Amount: ' + str(self.cocktail_amounts[cocktail_name][i]*self.shot_volume) + ' mL')
                self.adjust_volume_data(ingredient, self.cocktail_amounts[cocktail_name][i])

                #Finds which ingredient has the longest pump time required
                if(self.cocktail_amounts[cocktail_name][i] * self.pump_data[pump_num]['pumpTime'] > biggest_time):
                    biggest_time = (self.cocktail_amounts[cocktail_name][i]) * self.pump_data[pump_num]['pumpTime']
                i += 1
            
            wait_time = biggest_time
            print('Wait Time: ' + str(wait_time))
            time.sleep(wait_time)
            self.busy_flag = False
            self.start_time = 0.0
            self.current_cocktail = ''
            print("Done making cocktail!")

        except Exception as e:
            print(e)
            self.busy_flag = False
            return 'error'

        #Update cloud details (separate from above to avoid returning error if this fails)
        try:
            #Update Stat tracking in the cloud
            increment_cocktail(cocktail_name)
        except Exception as e:
            print(e)

        return 'true'

    #Toggles specific pumps for specific amount of time
    def pump_toggle(self, num, amt):
        pump_pin = self.pump_data[num]['gpio']
        GPIO.output(pump_pin, GPIO.LOW)
        time.sleep(self.pump_data[num]['pumpTime']*amt)
        GPIO.output(pump_pin, GPIO.HIGH)

    #Turns on a specific pump for indefinite amount of time
    def pump_on(self, num):
        pump_pin = self.pump_data[num]['gpio']
        print('Turning on pump: ' + str(num))
        GPIO.output(pump_pin, GPIO.LOW)

    #Turns off a specific pump for indefinite amount of time
    def pump_off(self, num):
        pump_pin = self.pump_data[num]['gpio']
        print("Turning off pump: " + str(num))
        GPIO.output(pump_pin, GPIO.HIGH)

    #Turn pressure pump on
    def pressure_on(self, num):
        pin = self.pressure_pins[str(num)]
        print('Turning on pressure pump: ' + str(num))
        GPIO.output(pin, GPIO.LOW)

    #Turn pressure pump off
    def pressure_off(self, num):
        pin = self.pressure_pins[str(num)]
        print('Turning on pressure pump: ' + str(num))
        GPIO.output(pin, GPIO.HIGH)

    #Toggle pressure pump for certain amount of time
    def pressure_toggle(self, num, pressure_time):
        self.pressure_on(num)
        time.sleep(pressure_time)
        self.pressure_off(num)

    
    #Calibrates a specific pump by setting it's specific pumping time
    def calibrate_pump(self, pump_num, calib_time):
        try:
            self.pump_data[pump_num]['pumpTime'] = calib_time
            self.write_pump_data()
        except Exception as e:
            print('ERROR: CALIBRATING PUMP FAILED')
            print(e)
            return 'false'

        return 'true' #Success
    
    #Reverse the polarity of the motors
    def reverse_polarity(self):
        if(self.polarity_normal):
            #Turn off signal for #1 relay
            GPIO.output(self.polarity_pins[0], GPIO.HIGH)

            #Turn on signal for #2 relay
            GPIO.output(self.polarity_pins[1], GPIO.LOW)
            self.polarity_normal = False
        else:
            #Turn on signal for #1 relay
            GPIO.output(self.polarity_pins[0], GPIO.LOW)

            # Turn off signal for #2 relay
            GPIO.output(self.polarity_pins[1], GPIO.HIGH)

            self.polarity_normal = True
        
        print('Done reversing polarities!')
        return self.polarity_normal


    #Cleans Pumps by flushin them for time specified in self.cleanTime
    def clean_pumps(self, remove_ignore=False):
        if(self.busy_flag and not remove_ignore):
            return 'busy'

        print('Cleaning pumps!')

        if(not remove_ignore):
            self.busy_flag = True

        #Turn all pumps on (except for soda pumps)
        for pump in self.pump_data:
            if(remove_ignore and self.pump_data[pump]['type'] == 'regular'):
                GPIO.output(self.pump_data[pump]['gpio'], GPIO.LOW)
            elif(not remove_ignore):
                GPIO.output(self.pump_data[pump]['gpio'], GPIO.LOW) #TODO: SEE IF THIS IS NECESSARY TO CHECK REMOVE_IGNORE

        time.sleep(self.clean_time)

        #Turn all pumps off (ignore soda pumps)
        for pump in self.pump_data:
            if(remove_ignore and self.pump_data[pump]['type'] == 'regular'):
                GPIO.output(self.pump_data[pump]['gpio'], GPIO.HIGH)
            elif(not remove_ignore):
                GPIO.output(self.pump_data[pump]['gpio'], GPIO.HIGH)
        
        if(not remove_ignore):
            self.busy_flag = False
        
        return 'true'

    #Adjusts the volume an ingredient after a certain amount is poured
    def adjust_volume_data(self, ingredient_name, shot_amount):
        print('Value: ' + str(self.pump_map[ingredient_name]['volume']))
        new_val = float(self.pump_map[ingredient_name]['volume']) - (self.shot_volume*shot_amount)
        print('New Value: ' + str(new_val))
        self.pump_map[ingredient_name]['volume'] = str(new_val)
        self.write_pump_data()


    #Assemble ingredient info packet for mobile app
    def get_ingredient_volume(self, ingredient):
        vol_obj = {}
        vol_obj['ingredient'] = ingredient
        vol_obj['volume'] = self.pump_map[ingredient]['volume']
        vol_obj['originalVolume'] = self.pump_map[ingredient]['originalVolume']
        percent = (int(vol_obj['volume']) / int(vol_obj['originalVolume']))*100
        vol_obj['percent'] = round(percent)

        return vol_obj

    
    #Checks whether it is possible to make a given cocktail
    def can_make_cocktail(self, name):
        i = 0
        for ingredient in self.cocktail_ingredients[name]:
            #Check for alcohol mode
            if(self.alcohol_mode and ingredient not in self.alcohol_list):
                i += 1
                continue
            #Check for ignore list
            if(ingredient in self.ignore_list):
                i += 1
                continue

            available_amt = float(self.pump_map[ingredient]['volume'])
            need_amt = float(self.cocktail_amounts[name][i])*self.shot_volume
            print('Ingredient: ' + ingredient + '   availableAmt: ' + str(available_amt) + '   needAmt: ' + str(need_amt))
            i += 1
            if((available_amt - need_amt) < 0):
                return False
        return True


    #Get the cocktail list from available ingredients
    def get_cocktail_list(self):
        available_cocktails = []
        count = 0
        for cocktail_name in self.cocktail_ingredients.keys():

            if(self.cocktail_available[cocktail_name]):
                available_cocktails.append(cocktail_name)
                count += 1
            else:
                print('Cocktail: ' + cocktail_name + ' is not available!')

        return available_cocktails

    
    #Get the ingredients of a specific cocktail from DynamoDB (CLOUD ONLY VERSION)
    def get_cloud_ingredients(self, name):
        response = get_recipe(name)
        recipe = {}

        #Convert Decimals back to floats
        for key in response['amounts']:
            recipe[key] = float(response['amounts'][key])

        return recipe

    #Get's ingredients for a specified recipe
    def get_ingredients(self, name):
        print("GETTING INGREDIENTS")
        recipe = {}

        i = 0
        for ingredient in self.cocktail_ingredients[name]:
            recipe[ingredient] = float(self.cocktail_amounts[name][i])
            i += 1

        return recipe

    #Get's the percentage full a bottle is
    def get_bottle_percentage(self, bottle_name):
        try:
            now = self.get_bottle_volume(bottle_name)
            full = self.get_bottle_init_volume(bottle_name)
            if(now == -1 or full == -1):
                return 'N/A'
            percent = (now/full)*100
            return str(int(percent))
        except Exception as e:
            print('Error getting bottle percentage!')
            print(e)
            return 'N/A'

    #Gets the current volume of a bottle
    def get_bottle_volume(self, bottle_name):
        if(bottle_name in self.pump_map):
            vol = round(float(self.pump_map[bottle_name]['volume']))
            return vol
        else:
            return -1

    #Gets the initial volume of a bottle
    def get_bottle_init_volume(self, bottle_name):
        if(bottle_name in self.pump_map):
            vol = round(float(self.pump_map[bottle_name]['originalVolume']))
            return vol
        else:
            return -1

    #Gets the name of the bottle on a given pump
    def get_bottle_name(self, bottle_num):
        try:

            #TODO: Make this more efficient; maybe have a hashmap
            for ingredient in self.pump_map:
                if(self.pump_map[ingredient]['pumpNum'] == bottle_num):
                    bottle_name = ingredient
                    return bottle_name
            return 'N/A'
        except Exception as e:
            print('Error getting bottle name!')
            print(e)
            return 'N/A'


    #Enables Barbot's "alcohol mode" (only outputting ingredients that alcohol)
    def set_alcohol_mode(self, mode_setting):
        self.alcohol_mode = mode_setting
        self.refresh_cocktail_files()
        print("Alcohol mode: " + str(mode_setting))

    
    #Remove all bottles from pumps
    def remove_all_bottles(self):

        if(self.busy_flag):
            return 'busy'
        
        try:
            self.busy_flag = True
            #First reverse the polarity
            self.reverse_polarity()

            #Make a copy of the bottles
            total_bottles = list(self.pump_map.keys())

            #Next remove all bottles
            for bottle_name in total_bottles:
                self.remove_bottle(bottle_name, skip_pumps=True)
            
            #Refresh files after removing all bottles
            self.refresh_cocktail_files()
            
            #Run a the clean function to turn on all pumps
            self.clean_pumps(remove_ignore=True)
            
            #Finally reverse the polarity again
            self.reverse_polarity()
            self.busy_flag = False
        except Exception as e:
            print(e)
            return 'error'
        return 'true'

    #Remove bottle from pumpMap
    def remove_bottle(self, bottle_name, skip_pumps=False):
        pump_num = self.pump_map[bottle_name]['pumpNum']

        if(bottle_name in self.pump_map and not self.busy_flag and not skip_pumps and self.pump_data[pump_num]['type'] == 'regular'):
            self.busy_flag = True
            
            #Reverse pump polarity
            self.reverse_polarity()
            
            #Turn on the designated pump
            self.pump_on(pump_num)

            #Pause for a few seconds
            time.sleep(self.clean_time)

            self.pump_off(pump_num)

            self.reverse_polarity()
            self.busy_flag = False
        elif(self.busy_flag and not skip_pumps):
            return 'busy'
        
        #Try to remove bottleName from pumpMap
        try:
            self.pump_map.pop(bottle_name)
        except KeyError as e:
            print('Error removing bottle')
            print(e)
            return 'false'

        self.add_new_bottle_to_list(bottle_name)

        #Don't want to refresh too many times
        if(not skip_pumps):
            self.refresh_cocktail_files()

        return 'true'

    #Adds bottle to pumpMap
    def add_bottle(self, bottle_name, pump_num, volume, original_volume):
        self.pump_map[bottle_name] = {}
        self.pump_map[bottle_name]['name'] = bottle_name
        self.pump_map[bottle_name]['pumpNum'] = pump_num
        self.pump_map[bottle_name]['volume'] = volume
        self.pump_map[bottle_name]['originalVolume'] = original_volume
        self.remove_bottle_from_list(bottle_name)
        self.refresh_cocktail_files()

    #Formats and writes pump_map and pump_data objects to the pumpConfig.json file
    def write_pump_data(self):
        main_arr = []
        pumps_done = set()

        for ingredient in self.pump_map:
            pump_num = self.pump_map[ingredient]['pumpNum']
            data_obj = self.pump_data[pump_num].copy()
            map_obj = self.pump_map[ingredient].copy()
            map_obj.pop('pumpNum')
            data_obj['currentBottle'] = map_obj
            pumps_done.add(pump_num)

            main_arr.append(data_obj)

        #Add pumps that aren't in pumpMap already (i.e. no bottle connected)
        for pump in self.pump_data:
            if(pump in pumps_done):
                continue
            
            data_obj = self.pump_data[pump]
            map_obj = {}
            data_obj['currentBottle'] = map_obj
            pumps_done.add(pump)

            main_arr.append(data_obj)
        
        with open('pumpConfig.json', 'w') as file:
            json.dump(main_arr, file)

        print('Wrote pump config to file')

    #Refreshes all of the local cache files
    def refresh_cocktail_files(self):
        try:
            print("Refreshing cocktail files...")
            self.write_pump_data()
            self.load_pump_config()
            self.update_local_recipes()
            self.load_cocktails()
            self.load_alcohol_list()
            self.load_ignore_list()
        except Exception as e:
            print(e)
            return 'error'
        return 'true'

    #Updates to newest software from git
    def update(self):
        try:
            subprocess.Popen('/home/pi/BarBot/update.sh')
        except Exception as e:
            print(e)

    #Reboot's BarBot Raspberry Pi
    def reboot(self):
        try:
            subprocess.Popen('/home/pi/BarBot/reboot.sh')
        except Exception as e:
            print(e)

        