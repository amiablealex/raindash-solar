# Runs automatically at power-on. In Thonny, press Stop to get the >>> prompt.
import epd
display = epd.EPD()          # take the 34 KB screen buffer first, while memory is unfragmented
import raindash
raindash.main(display)
