# Krillion Copilot Writeup

## 1. Problem and approach

Krillion is a daily trivia game. You're given seven prompts and are scored based on how unique your answer is. Each prompt gives you 25 seconds to answer. This project is an AI agent that suggests high-scoring answers in real time.

The pipeline goes in the following steps:

1. Route: The browser extension reads the question when it's loaded and sends it to the local backend server.
2. Generate: The agent generates 8 to 20 plausible answers.
3. Rule Check: The agent checks each generated answer and drops invalid or incorrect answers.
4. Rarity: Common, low-scoring answers are pruned and only the top unique answers are kept.
5. Validate: Answers that the agent has low confidence that they'll be accepted are dropped.

Some additional constraints and their reasoning
- Human types every answer: I wanted this project to be an AI assistant that helps players, not an agent that plays the game independently. Using tampermonkey I wrote a browser extension that overlays answers on top of the game page so a human can read the answers while playing.
- No internet search: The agent never searches the internet for valid answers. Practically, this would take too long in a normal round. Spiritually, it's more fun to have the AI generate everything itself. This made hallucination and timing a real challenge which was the meat of this project.

## 2. Evaluation
I ran the pipeline on 42 prompts from six past days and looked each suggestion up on that day's saved answer page. The pages don't list Deep Cut answers, so my point totals are lower bounds. The generator is random, so I ran the same code 10 times: the top pick averaged 18.4 points, 43% of answers were listed, and the top pick changed tier on 79% of prompts. A single run can't detect a change smaller than about 9 percentage points in listed share, so I compared versions over 5 to 10 runs.

## 3. Trials and failures
I tried replacing the batched validator with one yes/no-style call per answer that reads the model's probabilities. It dropped 28% of answers I labeled real against 11% for the batched validator, so I switched back.

The model can't reliably check conditions on a single answer, such as "food that has an emoji" or "country whose second letter is A". It confidently accepts answers that fail them and it can't count letters. I decided that the pipeline should hand letter rules to code, so I tried four ways to catch the other conditions: a prompt rule telling the validator that an answer failing a written condition is invalid, a separate yes/no question per condition, extracting the conditions in the route step and demoting answers that fail them, and leniency wording to stop it dropping valid answers. None worked, because the model's knowledge of the facts was the problem and not the prompt.

Removing the category label from the validator's input fixed the Minecraft prompt that sometimes returned nothing. Two prompts still return little or nothing: the 2025/2026 songs prompt, where the model invents titles that the validator correctly drops, and the country-by-area prompt, where the generator keeps picking countries that are too small.

## 4. Conclusion
The final pipeline basically scores the same as the original within noise (43.1% listed against 43.0%), so the one lasting change is the Minecraft fix. Most of what looked like improvements in early runs was just noise, so measuring the noise first was the most critical thing I did. Hidden conditions and questions the model can't know are still open problems.

| Metric | Baseline (10 runs) | Final branch (8 runs) | Original `main` (5 runs) |
| --- | --- | --- | --- |
| Answers listed on the sheet | 43.0% | 43.1% | 43.7% |
| Estimated points per candidate | 50.7 | 51.0 | 49.9 |
| Top pick points (lower bound) | 18.4 | 19.1 | 18.1 |
| Seconds per prompt | 4.89 | 4.90 | 4.54 |

