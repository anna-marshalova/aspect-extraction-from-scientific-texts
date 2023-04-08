from typing import List, Set, Union, Dict
from collections import defaultdict
import pymorphy2
import spacy
from spacy.tokens.token import Token
from spacy.tokens.doc import Doc

from predictor import Predictor

class AspectExtractor:
    """ Класс для извлечения аспектов как словосочетаний"""
    def __init__(self, predictor:Predictor, normalize:bool=True):
        """
        :param predictor: Класс для получения предсказаний модели
        :param normalize: Ставить ли извлеченные аспекты в начальную форму
        """
        self.PAIRED_L2R = {'(': ')', '[': ']', '«': '»', '{': '}'}
        self.PAIRED_R2L = dict(zip(self.PAIRED_L2R.values(), self.PAIRED_L2R.keys()))
        self.UNPAIRED_PUNCT = ".,:;!?%^"
        self.SYMM_QUOTES = ['"']
        self.TRANSLATIONS = {'Task': ('Задача', 'Задачи'), 'Method': ('Метод', 'Методы'), 'Contrib': ('Вклад', 'Вклад'),
                             'Conc': ('Вывод', 'Выводы')}
        # маппинг обозначений граммем из spacy в pymorphy2
        self.GRAMMEME_MAPPING = {'Fem': 'femn', 'Masc': 'masc', 'Neut': 'neut', 'Plur': 'plur', 'Sing': 'sing'}
        #части речи, согласуемые с существительным
        self.NOUN_DEP = ['appos', 'acl', 'amod', 'det', 'clf', 'nummod']
        self.do_normalize = normalize
        self.predictor = predictor
        if self.do_normalize:
            self.syntax_parser = spacy.load("ru_core_news_lg")
            self.morph = pymorphy2.MorphAnalyzer()

    def get_noun_grammemes(self, token: Token) -> Set[str]:
        """
        Вспомогательная функция.
        Заменяет обозначения граммем, используемых в spacy, на обозначения, используемые в pymorphy2.
        Выбирает признак, по которому определения будут согласовываться с существительным: число (для мн.ч.) или род (для ед.ч.).
        :param token: Токен с морфологической разметкой. Объект класса Token из библиотеки spacy
        :return: Множество, содержащее некоторую граммему токена (число или род) в обозначении, используемом в pymorphy2.
        """
        number = self.GRAMMEME_MAPPING.get(''.join(token.morph.get("Number")), 'sing')
        gender = self.GRAMMEME_MAPPING.get(''.join(token.morph.get("Gender")), 'masc')
        if number == 'plur':
            return set([number]) #если число множественное, согласовывать будем по числу
        return set([gender]) #если число единственное, согласовываем по роду

    def inflect(self, token: str, pos: List[str] = [], grammemes=set(['nomn'])) -> str:
        """
        Склонение слова
        :param token: Слово
        :param pos: Часть речи слова
        :param grammemes: Граммемы, по которым будет изменяться слово
        :return: Результат склонения - слово в нужной форме
        """
        parses = self.morph.parse(token)
        parse = parses[0]
        for parse in parses:
            if any(parse.tag.POS.startswith(prefix) for prefix in pos if parse.tag.POS): #поиск формы нужной части речи
                parse = parse
                break
        form = parse.inflect(grammemes)
        if form:
            return form.word
        else:
            return token

    def normalize(self, tokens:List[str]) -> List[str]:
        """
        Нормализация словосочетания (постановка в начальную форму)
        :param tokens: Список слов словосочетания
        :return: Словосочетание в начальной форме. Пример: теплового воздействия лесных пожаров -> тепловое воздействие лесных пожаров
        """
        normalized_text = []
        doc = self.syntax_parser(Doc(self.syntax_parser.vocab, words=tokens))
        root = doc[::].root
        if root.pos_ == 'NOUN':
            grammemes = self.get_noun_grammemes(root)
            for token in doc[::]:
                # числительные, зависимые от существительных просто ставим в Им.п.
                if token.dep_ == 'nummod':
                    normalized_text.append(self.inflect(token.text))
                # существительные, управляемые числительными ставим в Род.п.
                elif any(child.dep_.startswith('nummod') for child in token.children):
                    normalized_text.append(self.inflect(token.text, pos=['NOUN'], grammemes=set(['gent'])))
                # главное существительное в словосочетании просто ставим в Им.п.
                elif token == root:
                    normalized_text.append(self.inflect(token.text, pos=['NOUN']))
                # согласуемые части речи (прилагательные, причастия) склоняем, чтобы согласовать с существительным
                elif token.head == root and token.dep_ in self.NOUN_DEP:
                    normalized_text.append(
                        self.inflect(token.text, pos=['ADJ', 'PRT'], grammemes=set(['nomn']) | grammemes))
                # в остальных случаях оставляем все как есть
                else:
                    normalized_text.append(token.text)
            return normalized_text
        return tokens

    def detokenize(self, text: List[str]) -> str:
        """
        Детокенизация словосочетания. Из списка токенов делаем строку, удаляем пробелы перед пунктуацией там, где это нужно
        :param text: Токенизированное словосочетание (список слов)
        :return: Дектокенизированное словосочетание (строка)
        """
        detokenized_text = []
        sep = ''
        for token in text:
            if token in self.UNPAIRED_PUNCT or token in self.PAIRED_R2L:
                detokenized_text.append(token)
            else:
                detokenized_text.append(sep + token)
                sep = ' '
                if token in self.PAIRED_L2R:
                    sep = ''
        return ''.join(detokenized_text)

    def balance_parentheses(self, text: List[str]) -> List[str]:
        """
        Балансирование парной пунктуации.
        Исправляет случаи, когда один из парных знаков препинания попал в аспект, а другой нет.
        :param text: Токенизированный текст
        :return: Токенизированный текст со сбалансированной парной пунктуации
        """
        left_stack = []
        right_stack = []
        for token in text:
            if token in self.SYMM_QUOTES and token in left_stack:
                left_stack.remove(token)
            # кладем левые знаки препинания в стек
            elif token in self.PAIRED_L2R or token in self.SYMM_QUOTES:
                left_stack.insert(0, token)
            # удаляем их из стека, если они закрываются соотвествующими правыми знаками
            elif token in self.PAIRED_R2L and self.PAIRED_R2L[token] in left_stack:
                left_stack.remove(self.PAIRED_R2L[token])
            # правые знаки, несбалансированные левыми кладем в другой стек
            elif token in self.PAIRED_R2L and self.PAIRED_R2L[token] not in left_stack:
                right_stack.insert(0, token)
        # если в стеке остались правые знаки, добавляем в конец нужное количество соотвествующих им левых
        for p in right_stack:
            text.insert(0, self.PAIRED_R2L.get(p, p))
        # если в стеке остались левые знаки, добавляем в конец нужное количество соотвествующих им правых
        for p in left_stack:
            text.append(self.PAIRED_L2R.get(p, p))
        return text

    def process(self, text:List[str]) -> str:
        """
        Обработка текста аспекта: нормализация, балансирование парной пунктуации, детокенизация, капитализация
        :param text: Токенизированный текст
        :return: Обработанный текст
        """
        if self.do_normalize:
            text = self.normalize(text)
        text = self.balance_parentheses(text)
        text = self.detokenize(text)
        text = text[0].upper() + text[1:]
        return text

    def extract_aspects(self, text:Union[List[str],str]) -> Dict[str,List[str]]:
        """
        Извлечение аспектов из текста
        :param text: Текст (токенизированный или нет)
        :return: Словарь, где ключи - аспекты, значения - списки упоминаний каждого из аспектов
        """
        extracted_aspects = defaultdict(list)
        result = self.predictor.extract(text)
        for i, (cur_token, cur_label) in enumerate(result):
            if i == 0:
                prev_token, prev_label = ('', '')
            else:
                prev_token, prev_label = result[i - 1]
            aspects_for_token = cur_label.split('|')
            if 'O' in aspects_for_token:
                aspects_for_token = []
            for aspect in aspects_for_token:
                if aspect not in prev_label:
                    extracted_aspects[aspect].append([])
                extracted_aspects[aspect][-1].append(cur_token)
        return {aspect: [self.process(mention) for mention in mentions] for aspect, mentions in
                extracted_aspects.items()}

    def print_extracted_aspects(self, extracted_aspects:Dict[str,List[str]]):
        """
        Вывод извлеченных аспектов на экра.
        Пример:
        МЕТОДЫ
        1. Лагранжев бессеточный метод сглаженных частиц (SPH)
        2. Эйлеровые методы с использованием адаптивных сеток (AMR)
        ВКЛАД
        1. Перечислены различные свойства этих подходов и их влияние
        :param extracted_aspects: Словарь извлеченных аспектов, где ключи - аспекты, значения - списки упоминаний каждого из аспектов
        """
        for aspect, mentions in extracted_aspects.items():
            if len(mentions) == 1:
                aspect_name_ru = self.TRANSLATIONS[aspect][0]
            else:
                aspect_name_ru = self.TRANSLATIONS[aspect][1]
            print(aspect_name_ru.upper())
            for i, mention in enumerate(mentions):
                print(f'{i + 1}. {mention}')