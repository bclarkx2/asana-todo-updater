#!/bin/bash

for arg in "$@" 
do
( source readenv .env && python main.py "${arg}" )
done
