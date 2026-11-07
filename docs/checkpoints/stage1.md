# Stage 1 checkpoint

Stage 1 isn't finished until you can do this without AI help. Your essays and video will describe
this work, so these are the things you need to be able to explain and defend.

## Explain it

Answer in your own words, out loud or in your private log. Use the code in `mars_rover/terrain/`.

1. Why does each tile get read with extra cells around it? What would go wrong without them?
2. A map cell at Jezero is "1 m" wide, but really about 0.95 m. Why? Work it out from the latitude.
3. Why does a smooth, steep hill score zero roughness? Why is that useful?
4. Why does `step_height` estimate the slope from a 21-cell window, and not its 3-cell window?
5. Horn's slope formula doesn't use the center cell. What bug did that cause, and how was it fixed?
6. The map matches NASA's stop elevations to about 4 cm. Why isn't that strong evidence?
7. Why are horizontal lines in the map a likely flaw, and not real terrain?

## Change it

1. **Break it on purpose:** set `margin` to 0 in `process_terrain`, then run the tests. Explain which
   test fails and why. Put it back afterward.
2. **Predict, then test:** change the roughness window from 11 to 31 on the sample section. Write down
   what you expect to change before running it, then compare.
3. **Write your own metric:** add curvature, a layer showing ridges and hollows, with a test on a shape
   whose answer you know. Write this one yourself.

## Make a research decision

These are your calls. Write down your choice and your reasoning:

1. How should the planner handle the row artifacts: filter them, ignore them, or show first that they
   don't matter? What evidence would settle it?
2. Are 11 m and 3 m the right window sizes for a rover the size of Perseverance? Find its dimensions in
   a published source and justify your answer.
