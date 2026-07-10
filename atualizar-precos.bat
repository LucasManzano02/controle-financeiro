@echo off
rem Botão de atualização de preços — duplo clique para rodar
cd /d "%~dp0"
py update_prices.py %*
if errorlevel 1 python update_prices.py %*
pause
