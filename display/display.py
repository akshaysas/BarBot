import sys
import time
import json
import tkinter as tk
import requests
import urllib.parse
import socket

class Display():
    
    #====================TODO====================#
    #Need to create local rest api endpoints for the existing
    #cocktail making functions and other information retrieval.#

    def __init__(self):
        self.cocktailButtons = {}
        self.window = None
        self.controllerHost = 'http://' + self.getIpAddress() + ':5000'
        self.cocktailNames = []
        self.getCocktailNames()
        self.createGUI()
    
    #TODO change this to be the static ip address of the pi
    def getCocktailNames(self):
        res = requests.get(self.controllerHost + '/cocktailList/')
        
        if(res.status_code != 200):
            print('There was an error!')
            return
        else:
            self.cocktailNames = res.json()
            print(self.cocktailNames)

    def makeCocktail(self, name):
        urlName = urllib.parse.quote(name)
        res = requests.get(self.controllerHost + '/' + urlName + '/')

        if(res.status_code != 200):
            print('There was an error!')


    def cleanPumps(self):
        res = requests.get(self.controllerHost + '/clean/')

        if(res.status_code != 200):
            print('There was an error!')

    #Creates the GUI interface for selecting a cocktail
    def createGUI(self):
        self.window = tk.Tk()
        self.window.grid()
        self.window.geometry('480x320')
        self.window.title('BarBot - Beta Version 1.0')
        i = 0
        buttonCol = 0
        buttonRow = 0
        for drink in self.cocktailNames:
            if(buttonCol == 3):
                buttonCol = 0
                buttonRow += 1
            name = self.cocktailNames[i]
            self.cocktailButtons[i] = tk.Button(self.window, text=name, width = 20, height =10, command= lambda name=name: self.makeCocktail(name))
            self.cocktailButtons[i].grid(column=buttonCol, row=buttonRow)
            buttonCol += 1
            i = i+1
        buttonCol = 1
        buttonRow += 1
        cleanButton = tk.Button(self.window, text='Clean Pumps', width = 8, height = 4, command=self.cleanPumps)
        cleanButton.grid(row=buttonRow, column=buttonCol)
        buttonRow += 1
        stopButton = tk.Button(self.window, text='STOP', width = 4, height = 2, command=self.window.destroy)
        stopButton.grid(row=buttonRow, column=buttonCol)
        self.window.mainloop()


    def getIpAddress(self):
        try:
            addr = socket.gethostbyname('barbot.local')
            return addr
        except socket.herror:
            print('ERROR RESOLVING BARBOT HOSTNAME!')
            exit()

if __name__ == "__main__":
    display = Display()
    print('Exitting...')
    